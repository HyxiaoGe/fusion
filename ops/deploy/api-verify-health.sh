#!/usr/bin/env bash
set -eo pipefail

if [ "${OPS_DEPLOY_DRY_RUN:-false}" = "true" ]; then
  if [ "${GITHUB_ACTIONS:-false}" = "true" ]; then
    printf "GitHub Actions 不允许部署脚本 dry-run\n" >&2
    exit 1
  fi
  printf "DRY-RUN %s\n" "${BASH_SOURCE[0]##*/}"
  exit 0
fi

# OPS_DEPLOY_BODY_BEGIN
healthy=0
for attempt in $(seq 1 30); do
  if curl -fsS "${API_HEALTH_CHECK_ENDPOINT}" | grep -q '"status":"healthy"'; then
    healthy=1
    break
  fi
  sleep 2
done

if [ "$healthy" != "1" ]; then
  docker logs --tail=120 fusion-api || true
  exit 1
fi

adapter_healthy=0
for attempt in $(seq 1 30); do
  if docker exec fusion-flyai-adapter node -e \
    "fetch('http://127.0.0.1:8080/health',{signal:AbortSignal.timeout(2000)}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"; then
    adapter_healthy=1
    break
  fi
  sleep 2
done
if [ "$adapter_healthy" != "1" ]; then
  docker logs --tail=120 fusion-flyai-adapter || true
  exit 1
fi

if [ "${KNOWLEDGE_WORKER_SUPPORTED}" = "true" ]; then
  worker_healthy=0
  for attempt in $(seq 1 30); do
    if [ "$(docker inspect fusion-knowledge-worker --format '{{.State.Health.Status}}' 2>/dev/null || true)" = "healthy" ]; then
      worker_healthy=1
      break
    fi
    sleep 2
  done
  if [ "${worker_healthy}" != "1" ]; then
    docker logs --tail=120 fusion-knowledge-worker || true
    exit 1
  fi
  docker exec fusion-knowledge-worker python -m scripts.run_knowledge_worker \
    --healthcheck --health-max-age-seconds 120
fi

docker exec -i fusion-api python - <<'PY'
from app.core.config import settings

if not settings.ENABLE_FLYAI_TRAVEL_TOOLS:
    raise SystemExit("FlyAI travel tools are disabled")
if settings.FLYAI_ADAPTER_BASE_URL != "http://flyai-adapter:8080":
    raise SystemExit("FlyAI adapter base URL mismatch")
if not settings.FLYAI_ADAPTER_TOKEN:
    raise SystemExit("FlyAI adapter token is missing")
print("FlyAI adapter configuration ok")
PY

docker exec -i fusion-api python - <<'PY'
from app.core.config import settings

if "mcp.context7.com" not in settings.RESOLVED_MCP_ALLOWED_HOSTS:
    raise SystemExit("Context7 MCP host policy is missing")
if "CONTEXT7_API_KEY" not in settings.RESOLVED_MCP_ALLOWED_CREDENTIAL_REFS:
    raise SystemExit("Context7 MCP credential policy is missing")
print("Context7 MCP policy configuration ok")
PY

docker exec -i fusion-api python - <<'PY'
from app.core.config import settings

knowledge_enabled = getattr(settings, "KNOWLEDGE_BASE_ENABLED", None)
if knowledge_enabled is None:
    print("旧版回滚目标不含知识库配置，跳过该项兼容性探针")
elif knowledge_enabled:
    settings.validate_knowledge_base_configuration()
    if settings.MILVUS_USERNAME.casefold() == "root":
        raise SystemExit("knowledge Milvus account cannot be root")
    print("knowledge base configuration ok")
else:
    print("knowledge base feature disabled")
PY

docker inspect fusion-api --format '{{range .Mounts}}{{println .Destination}}{{end}}' | grep -qx '/app/storage/files'
docker inspect fusion-api --format '{{range .Mounts}}{{println .Destination}}{{end}}' | grep -qx '/var/lib/fusion/litellm-governance'
docker exec -e FUSION_ROLLBACK_REQUESTED="${ROLLBACK_REQUESTED}" -i fusion-api python - <<'PY'
import os

from app.core.config import settings

required_names = (
    "LITELLM_GOVERNANCE_ROOT",
    "LITELLM_GOVERNANCE_MAX_AGE_SECONDS",
    "LITELLM_MODEL_ADMISSION_WORKER_ENABLED",
    "LITELLM_MODEL_MANAGEMENT_ENABLED",
    "LITELLM_MODEL_ADMISSION_WORKER_TOKEN",
)
missing_names = [name for name in required_names if getattr(settings, name, None) is None]
rollback_requested = os.environ.get("FUSION_ROLLBACK_REQUESTED") == "true"
if missing_names:
    if rollback_requested:
        print("旧版回滚目标不含模型治理配置，跳过该项兼容性探针")
        raise SystemExit(0)
    raise SystemExit(f"LiteLLM model management settings missing: {', '.join(missing_names)}")

if getattr(settings, "LITELLM_GOVERNANCE_ROOT", None) != "/var/lib/fusion/litellm-governance":
    raise SystemExit("LiteLLM governance mount path mismatch")
if getattr(settings, "LITELLM_GOVERNANCE_MAX_AGE_SECONDS", 0) <= 0:
    raise SystemExit("LiteLLM governance max age must be positive")
worker_enabled = bool(getattr(settings, "LITELLM_MODEL_ADMISSION_WORKER_ENABLED", False))
management_enabled = bool(getattr(settings, "LITELLM_MODEL_MANAGEMENT_ENABLED", False))
worker_token = getattr(settings, "LITELLM_MODEL_ADMISSION_WORKER_TOKEN", "")
if worker_enabled and not management_enabled:
    raise SystemExit("LiteLLM admission worker cannot be enabled while model management is disabled")
if worker_enabled and not worker_token:
    raise SystemExit("LiteLLM admission worker token is missing")
print("LiteLLM model management read-only boundary ok")
PY
docker exec -i fusion-api python - <<'PY'
import asyncio
import urllib.request
import uuid

from app.core.config import settings

if settings.STORAGE_BACKEND == "local" and settings.FILE_STORAGE_PATH != "/app/storage/files":
    raise SystemExit(f"file storage path mismatch: {settings.FILE_STORAGE_PATH}")
if settings.STORAGE_BACKEND == "oss":
    missing = [
        name for name, value in {
            "OSS_ENDPOINT": settings.OSS_ENDPOINT,
            "OSS_ACCESS_KEY_ID": settings.OSS_ACCESS_KEY_ID,
            "OSS_ACCESS_KEY_SECRET": settings.OSS_ACCESS_KEY_SECRET,
            "OSS_BUCKET": settings.OSS_BUCKET,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit(f"oss storage config missing: {', '.join(missing)}")
    from app.services.storage.oss_storage import OSSStorageBackend

    storage = OSSStorageBackend(
        endpoint=settings.OSS_ENDPOINT,
        access_key_id=settings.OSS_ACCESS_KEY_ID,
        access_key_secret=settings.OSS_ACCESS_KEY_SECRET,
        bucket=settings.OSS_BUCKET,
        use_ssl=settings.OSS_USE_SSL,
    )
    upload = asyncio.run(
        storage.get_upload_url(
            "health/cors-preflight.txt",
            content_type="text/plain",
            expires=60,
        )
    )
    origin = settings.FRONTEND_URL.rstrip("/")
    request = urllib.request.Request(
        upload["url"],
        method="OPTIONS",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        headers = response.headers
    allow_origin = headers.get("Access-Control-Allow-Origin", "")
    allow_methods = headers.get("Access-Control-Allow-Methods", "")
    allow_headers = headers.get("Access-Control-Allow-Headers", "")
    if allow_origin not in {"*", origin}:
        raise SystemExit("oss cors origin is not allowed")
    if "PUT" not in allow_methods.upper():
        raise SystemExit("oss cors PUT method is not allowed")
    if "*" not in allow_headers and "CONTENT-TYPE" not in allow_headers.upper():
        raise SystemExit("oss cors Content-Type header is not allowed")
    key = f"health/monorepo-task2-{uuid.uuid4().hex}.txt"
    payload = b"fusion monorepo task2 oss acceptance\n"
    try:
        uploaded_key = asyncio.run(storage.upload(key, payload, "text/plain"))
        if uploaded_key != key:
            raise SystemExit("oss upload returned an unexpected key")
        downloaded = asyncio.run(storage.download(key))
        if downloaded != payload:
            raise SystemExit("oss upload/download payload mismatch")
        if asyncio.run(storage.get_size(key)) != len(payload):
            raise SystemExit("oss object size mismatch")
    finally:
        asyncio.run(storage.delete(key))
    if asyncio.run(storage.exists(key)):
        raise SystemExit("oss acceptance object cleanup failed")
    print("oss real upload/download acceptance passed")
print(f"file storage backend ok: {settings.STORAGE_BACKEND}")
PY

docker exec -i fusion-api python - <<'PY'
from app.core.config import settings
import urllib.request

url = settings.RESOLVED_AUTH_SERVICE_JWKS_URL
with urllib.request.urlopen(url, timeout=5) as response:
    if response.status != 200:
        raise SystemExit(f"auth JWKS check failed: http={response.status} url={url}")
print(f"auth JWKS ok: {url}")
PY
