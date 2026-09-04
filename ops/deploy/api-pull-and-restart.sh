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
cd "${FUSION_API_RUNTIME_DIR}"
set -o pipefail
set -a
source "${FUSION_RUNTIME_ENV}"
set +a
export CONTEXT7_API_KEY=""
export PROMPTHUB_API_KEY="${DEPLOY_PROMPTHUB_API_KEY:-}"
export PROMPTHUB_SYNC_MODE="${DEPLOY_PROMPTHUB_SYNC_MODE:-disabled}"
export RUN_CAPABILITY_CLASSIFIER_MODEL="${DEPLOY_RUN_CAPABILITY_CLASSIFIER_MODEL:-${RUN_CAPABILITY_CLASSIFIER_MODEL:-deepseek-chat}}"
export RUN_CAPABILITY_CLASSIFIER_TOKENIZER_MODEL="${DEPLOY_RUN_CAPABILITY_CLASSIFIER_TOKENIZER_MODEL:-${RUN_CAPABILITY_CLASSIFIER_TOKENIZER_MODEL:-deepseek/deepseek-chat}}"
export MCP_ALLOWED_HOSTS="${DEPLOY_MCP_ALLOWED_HOSTS:-${MCP_ALLOWED_HOSTS:-learn.microsoft.com,dashscope.aliyuncs.com,mcp.amap.com,mcp.context7.com}}"
export MCP_ALLOWED_CREDENTIAL_REFS="${DEPLOY_MCP_ALLOWED_CREDENTIAL_REFS:-${MCP_ALLOWED_CREDENTIAL_REFS:-DASHSCOPE_API_KEY,AMAP_MCP_API_KEY,CONTEXT7_API_KEY}}"
export MCP_CONNECT_TIMEOUT_SECONDS="${DEPLOY_MCP_CONNECT_TIMEOUT_SECONDS:-${MCP_CONNECT_TIMEOUT_SECONDS:-5}}"
export MCP_CALL_TIMEOUT_SECONDS="${DEPLOY_MCP_CALL_TIMEOUT_SECONDS:-${MCP_CALL_TIMEOUT_SECONDS:-15}}"
export MCP_IDEMPOTENT_TOTAL_TIMEOUT_SECONDS="${DEPLOY_MCP_IDEMPOTENT_TOTAL_TIMEOUT_SECONDS:-${MCP_IDEMPOTENT_TOTAL_TIMEOUT_SECONDS:-12}}"
export MCP_ADMIN_OPERATION_TIMEOUT_SECONDS="${DEPLOY_MCP_ADMIN_OPERATION_TIMEOUT_SECONDS:-${MCP_ADMIN_OPERATION_TIMEOUT_SECONDS:-35}}"
export MCP_MAX_DISCOVERY_PAGES="${DEPLOY_MCP_MAX_DISCOVERY_PAGES:-${MCP_MAX_DISCOVERY_PAGES:-5}}"
export MCP_MAX_DISCOVERED_TOOLS="${DEPLOY_MCP_MAX_DISCOVERED_TOOLS:-${MCP_MAX_DISCOVERED_TOOLS:-50}}"
export MCP_MAX_TOOL_DESCRIPTION_CHARS="${DEPLOY_MCP_MAX_TOOL_DESCRIPTION_CHARS:-${MCP_MAX_TOOL_DESCRIPTION_CHARS:-2000}}"
export MCP_MAX_TOOL_SCHEMA_BYTES="${DEPLOY_MCP_MAX_TOOL_SCHEMA_BYTES:-${MCP_MAX_TOOL_SCHEMA_BYTES:-32768}}"
export MCP_MAX_RESPONSE_BYTES="${DEPLOY_MCP_MAX_RESPONSE_BYTES:-${MCP_MAX_RESPONSE_BYTES:-262144}}"
export MCP_MAX_TOOL_CALLS_PER_SERVER_PER_RUN="${DEPLOY_MCP_MAX_TOOL_CALLS_PER_SERVER_PER_RUN:-${MCP_MAX_TOOL_CALLS_PER_SERVER_PER_RUN:-8}}"
export MCP_SERVER_CIRCUIT_FAILURE_THRESHOLD="${DEPLOY_MCP_SERVER_CIRCUIT_FAILURE_THRESHOLD:-${MCP_SERVER_CIRCUIT_FAILURE_THRESHOLD:-3}}"
export MCP_SERVER_CIRCUIT_COOLDOWN_SECONDS="${DEPLOY_MCP_SERVER_CIRCUIT_COOLDOWN_SECONDS:-${MCP_SERVER_CIRCUIT_COOLDOWN_SECONDS:-30}}"
export DASHSCOPE_API_KEY="${DEPLOY_DASHSCOPE_API_KEY:-${DASHSCOPE_API_KEY:-}}"
export AMAP_MCP_API_KEY="${DEPLOY_AMAP_MCP_API_KEY:-${AMAP_MCP_API_KEY:-}}"
export LITELLM_MODEL_MANAGEMENT_ENABLED="$(printf '%s' "${DEPLOY_LITELLM_MODEL_MANAGEMENT_ENABLED:-false}" | tr '[:upper:]' '[:lower:]')"
export LITELLM_MODEL_ADMISSION_WORKER_ENABLED="$(printf '%s' "${DEPLOY_LITELLM_MODEL_ADMISSION_WORKER_ENABLED:-false}" | tr '[:upper:]' '[:lower:]')"
export LITELLM_GOVERNANCE_MAX_AGE_SECONDS="${DEPLOY_LITELLM_GOVERNANCE_MAX_AGE_SECONDS:-86400}"
export LITELLM_MODEL_ADMISSION_WORKER_TOKEN="${DEPLOY_LITELLM_MODEL_ADMISSION_WORKER_TOKEN:-}"
export KNOWLEDGE_BASE_ENABLED="$(printf '%s' "${DEPLOY_KNOWLEDGE_BASE_ENABLED:-false}" | tr '[:upper:]' '[:lower:]')"
export KNOWLEDGE_MAX_BASES_PER_USER="${KNOWLEDGE_MAX_BASES_PER_USER:-50}"
export KNOWLEDGE_MAX_DOCUMENTS_PER_BASE="${KNOWLEDGE_MAX_DOCUMENTS_PER_BASE:-100}"
export KNOWLEDGE_MAX_FILE_SIZE="${DEPLOY_KNOWLEDGE_MAX_FILE_SIZE:-10485760}"
export KNOWLEDGE_ALLOWED_MIME_TYPES="${KNOWLEDGE_ALLOWED_MIME_TYPES:-text/plain,text/markdown,text/csv,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document}"
export KNOWLEDGE_PARSER_VERSION="${KNOWLEDGE_PARSER_VERSION:-parser-v1}"
export KNOWLEDGE_CHUNKER_VERSION="${DEPLOY_KNOWLEDGE_CHUNKER_VERSION:-chunker-v2}"
export KNOWLEDGE_EMBEDDING_MODEL="${DEPLOY_KNOWLEDGE_EMBEDDING_MODEL:-}"
export KNOWLEDGE_EMBEDDING_REVISION="${DEPLOY_KNOWLEDGE_EMBEDDING_REVISION:-}"
if [ -n "${DEPLOY_KNOWLEDGE_EMBEDDING_REVISION_ROUTES:-}" ]; then
  export KNOWLEDGE_EMBEDDING_REVISION_ROUTES="${DEPLOY_KNOWLEDGE_EMBEDDING_REVISION_ROUTES}"
else
  export KNOWLEDGE_EMBEDDING_REVISION_ROUTES="{}"
fi
export KNOWLEDGE_EMBEDDING_PROVIDER="${KNOWLEDGE_EMBEDDING_PROVIDER:-litellm}"
export KNOWLEDGE_EMBEDDING_DIMENSION="${DEPLOY_KNOWLEDGE_EMBEDDING_DIMENSION:-1024}"
export KNOWLEDGE_EMBEDDING_ALLOWED_DIMENSIONS="${DEPLOY_KNOWLEDGE_EMBEDDING_ALLOWED_DIMENSIONS:-1024}"
export KNOWLEDGE_CHUNK_SIZE="${DEPLOY_KNOWLEDGE_CHUNK_SIZE:-1200}"
export KNOWLEDGE_CHUNK_OVERLAP="${DEPLOY_KNOWLEDGE_CHUNK_OVERLAP:-200}"
export KNOWLEDGE_MAX_CHUNKS_PER_DOCUMENT="${DEPLOY_KNOWLEDGE_MAX_CHUNKS_PER_DOCUMENT:-10000}"
export KNOWLEDGE_PARSE_TIMEOUT_SECONDS="${DEPLOY_KNOWLEDGE_PARSE_TIMEOUT_SECONDS:-60}"
export KNOWLEDGE_EMBEDDING_BATCH_SIZE="${DEPLOY_KNOWLEDGE_EMBEDDING_BATCH_SIZE:-32}"
export KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS="${DEPLOY_KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS:-30}"
export KNOWLEDGE_SEARCH_MAX_PROFILES="${DEPLOY_KNOWLEDGE_SEARCH_MAX_PROFILES:-8}"
export KNOWLEDGE_DISTANCE_METRIC="${KNOWLEDGE_DISTANCE_METRIC:-COSINE}"
export KNOWLEDGE_WORKER_POLL_SECONDS="${DEPLOY_KNOWLEDGE_WORKER_POLL_SECONDS:-2}"
export KNOWLEDGE_WORKER_LEASE_SECONDS="${DEPLOY_KNOWLEDGE_WORKER_LEASE_SECONDS:-180}"
export KNOWLEDGE_WORKER_HEARTBEAT_SECONDS="${DEPLOY_KNOWLEDGE_WORKER_HEARTBEAT_SECONDS:-30}"
export KNOWLEDGE_WORKER_MAX_ATTEMPTS="${DEPLOY_KNOWLEDGE_WORKER_MAX_ATTEMPTS:-5}"
export KNOWLEDGE_WORKER_RETRY_BASE_SECONDS="${KNOWLEDGE_WORKER_RETRY_BASE_SECONDS:-5}"
export KNOWLEDGE_WORKER_RETRY_MAX_SECONDS="${KNOWLEDGE_WORKER_RETRY_MAX_SECONDS:-300}"
export KNOWLEDGE_WORKER_HEALTH_FILE="${KNOWLEDGE_WORKER_HEALTH_FILE:-/tmp/fusion-knowledge-worker-health.json}"
export MILVUS_URI="${DEPLOY_MILVUS_URI:-}"
export MILVUS_USERNAME="${DEPLOY_MILVUS_USERNAME:-}"
export MILVUS_PASSWORD="${DEPLOY_MILVUS_PASSWORD:-}"
export MILVUS_DATABASE="${DEPLOY_MILVUS_DATABASE:-}"
export MILVUS_COLLECTION_PREFIX="${DEPLOY_MILVUS_COLLECTION_PREFIX:-fusion_knowledge_chunks}"
export MILVUS_TIMEOUT_SECONDS="${DEPLOY_MILVUS_TIMEOUT_SECONDS:-10}"
export MILVUS_DOCKER_NETWORK="${DEPLOY_MILVUS_DOCKER_NETWORK:-fusion_knowledge_milvus}"
case "${LITELLM_GOVERNANCE_MAX_AGE_SECONDS}" in
  ''|*[!0-9]*|0) echo "治理快照最大年龄必须是正整数"; exit 1 ;;
esac
case "${LITELLM_MODEL_MANAGEMENT_ENABLED}:${LITELLM_MODEL_ADMISSION_WORKER_ENABLED}" in
  true:true|true:false|false:false) ;;
  *) echo "模型管理开关必须为 true 或 false，且 Worker 不能单独启用"; exit 1 ;;
esac
if [ "${LITELLM_MODEL_ADMISSION_WORKER_ENABLED}" = "true" ] && [ -z "${LITELLM_MODEL_ADMISSION_WORKER_TOKEN}" ]; then
  echo "模型准入 Worker 已启用，但发布密钥未配置"
  exit 1
fi
case "${KNOWLEDGE_BASE_ENABLED}" in
  true|false) ;;
  *) echo "KNOWLEDGE_BASE_ENABLED 必须为 true 或 false"; exit 1 ;;
esac
case "${MILVUS_DOCKER_NETWORK}" in
  ''|*[!a-zA-Z0-9_.-]*) echo "MILVUS_DOCKER_NETWORK 格式无效"; exit 1 ;;
esac
if [ "${KNOWLEDGE_BASE_ENABLED}" = "true" ]; then
  if [ -z "${KNOWLEDGE_EMBEDDING_MODEL}" ] \
    || [ -z "${KNOWLEDGE_EMBEDDING_REVISION}" ] \
    || [ -z "${MILVUS_URI}" ] \
    || [ -z "${MILVUS_USERNAME}" ] \
    || [ -z "${MILVUS_PASSWORD}" ] \
    || [ -z "${MILVUS_DATABASE}" ]; then
    echo "知识库已启用，但 Embedding 或 Milvus 应用账号配置不完整"
    exit 1
  fi
  if [ "$(printf '%s' "${MILVUS_USERNAME}" | tr '[:upper:]' '[:lower:]')" = "root" ]; then
    echo "知识库禁止使用 Milvus root 账号"
    exit 1
  fi
fi
# BEGIN CURRENT_KNOWLEDGE_REVISION_ROUTES
read_current_knowledge_snapshot_value() {
  snapshot_path="$1"
  value_name="$2"
  case "${snapshot_path}" in
    "${RUNNER_TEMP}"/fusion-knowledge-rollback-*.env) ;;
    *) echo "知识库回滚配置未落在 RUNNER_TEMP 的受管路径中" >&2; return 1 ;;
  esac
  if [ ! -f "${snapshot_path}" ] || [ -L "${snapshot_path}" ]; then
    echo "知识库回滚配置快照缺失或不是受管普通文件" >&2
    return 1
  fi
  snapshot_mode="$(python3 -c 'import os, stat, sys; print(format(stat.S_IMODE(os.stat(sys.argv[1], follow_symlinks=False).st_mode), "o"), end="")' "${snapshot_path}")" || return 1
  if [ "${snapshot_mode}" != "600" ]; then
    echo "知识库回滚配置快照权限无效" >&2
    return 1
  fi
  (
    unset KNOWLEDGE_BASE_ENABLED KNOWLEDGE_EMBEDDING_REVISION_ROUTES MILVUS_URI MILVUS_DATABASE
    source "${snapshot_path}"
    case "${value_name}" in
      revision_routes)
        if [ -n "${KNOWLEDGE_EMBEDDING_REVISION_ROUTES:-}" ]; then
          printf '%s' "${KNOWLEDGE_EMBEDDING_REVISION_ROUTES}"
        else
          printf '%s' '{}'
        fi
        ;;
      enabled) printf '%s' "${KNOWLEDGE_BASE_ENABLED:-false}" ;;
      milvus_uri) printf '%s' "${MILVUS_URI:-}" ;;
      milvus_database) printf '%s' "${MILVUS_DATABASE:-}" ;;
      *) return 1 ;;
    esac
  )
}
rollback_knowledge_config_file="${RUNNER_TEMP}/fusion-knowledge-rollback-${GITHUB_RUN_ID}-${GITHUB_JOB}-${GITHUB_RUN_ATTEMPT}.env"
current_knowledge_revision_routes="$(read_current_knowledge_snapshot_value "${rollback_knowledge_config_file}" revision_routes)" || exit 1
export CURRENT_KNOWLEDGE_EMBEDDING_REVISION_ROUTES="${current_knowledge_revision_routes}"
export CURRENT_KNOWLEDGE_BASE_ENABLED="$(read_current_knowledge_snapshot_value "${rollback_knowledge_config_file}" enabled)" || exit 1
export CURRENT_MILVUS_URI="$(read_current_knowledge_snapshot_value "${rollback_knowledge_config_file}" milvus_uri)" || exit 1
export CURRENT_MILVUS_DATABASE="$(read_current_knowledge_snapshot_value "${rollback_knowledge_config_file}" milvus_database)" || exit 1
# END CURRENT_KNOWLEDGE_REVISION_ROUTES
validated_revision_routes="$(python3 - <<'PY'
import json
import math
import os
import re

def finite_number(name):
    try:
        value = float(os.environ[name])
    except (KeyError, ValueError):
        raise SystemExit("knowledge deployment bounds invalid")
    if not math.isfinite(value):
        raise SystemExit("knowledge deployment bounds invalid")
    return value

def revision_routes(name, *, allow_empty, error):
    try:
        routes = json.loads(os.environ[name])
    except (KeyError, json.JSONDecodeError):
        raise SystemExit(error)
    if not isinstance(routes, dict) or (not allow_empty and not routes):
        raise SystemExit(error)
    for key, alias in routes.items():
        if not isinstance(key, str) or not isinstance(alias, str):
            raise SystemExit(error)
        model, separator, revision = key.rpartition("@")
        if (
            not separator
            or re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", model) is None
            or re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", revision) is None
            or re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", alias) is None
            or alias.startswith("litellm_proxy/")
        ):
            raise SystemExit(error)
    return routes

enabled = os.environ.get("KNOWLEDGE_BASE_ENABLED") == "true"
routes = revision_routes(
    "KNOWLEDGE_EMBEDDING_REVISION_ROUTES",
    allow_empty=not enabled,
    error="knowledge embedding revision routes invalid",
)
current_routes = revision_routes(
    "CURRENT_KNOWLEDGE_EMBEDDING_REVISION_ROUTES",
    allow_empty=True,
    error="current knowledge embedding revision routes invalid",
)
if any(routes.get(key) != alias for key, alias in current_routes.items()):
    raise SystemExit("knowledge embedding revision routes history changed")

try:
    worker_lease = int(os.environ["KNOWLEDGE_WORKER_LEASE_SECONDS"])
    worker_max_attempts = int(os.environ["KNOWLEDGE_WORKER_MAX_ATTEMPTS"])
    worker_retry_base = int(os.environ["KNOWLEDGE_WORKER_RETRY_BASE_SECONDS"])
    worker_retry_max = int(os.environ["KNOWLEDGE_WORKER_RETRY_MAX_SECONDS"])
except (KeyError, ValueError):
    raise SystemExit("knowledge cleanup worker bounds invalid")
worker_poll = finite_number("KNOWLEDGE_WORKER_POLL_SECONDS")
if not (
    0 < worker_poll <= 60
    and worker_lease > 0
    and worker_max_attempts > 0
    and 0 < worker_retry_base <= worker_retry_max <= 3600
):
    raise SystemExit("knowledge cleanup worker bounds invalid")

current_milvus_uri = os.environ.get("CURRENT_MILVUS_URI", "")
current_milvus_database = os.environ.get("CURRENT_MILVUS_DATABASE", "")
if (current_milvus_uri or current_milvus_database) and (
    current_milvus_uri != os.environ.get("MILVUS_URI", "")
    or current_milvus_database != os.environ.get("MILVUS_DATABASE", "")
):
    raise SystemExit("knowledge milvus storage route changed")

if enabled:
    if os.environ.get("KNOWLEDGE_CHUNKER_VERSION") != "chunker-v2":
        raise SystemExit("knowledge chunker version invalid")
    try:
        max_bases_per_user = int(os.environ["KNOWLEDGE_MAX_BASES_PER_USER"])
        max_documents_per_base = int(os.environ["KNOWLEDGE_MAX_DOCUMENTS_PER_BASE"])
        max_file_size = int(os.environ["KNOWLEDGE_MAX_FILE_SIZE"])
        chunk_size = int(os.environ["KNOWLEDGE_CHUNK_SIZE"])
        chunk_overlap = int(os.environ["KNOWLEDGE_CHUNK_OVERLAP"])
        max_chunks_per_document = int(os.environ["KNOWLEDGE_MAX_CHUNKS_PER_DOCUMENT"])
        parse_timeout = int(os.environ["KNOWLEDGE_PARSE_TIMEOUT_SECONDS"])
        embedding_dimension = int(os.environ["KNOWLEDGE_EMBEDDING_DIMENSION"])
        embedding_allowed_dimensions = {
            int(value.strip())
            for value in os.environ["KNOWLEDGE_EMBEDDING_ALLOWED_DIMENSIONS"].split(",")
            if value.strip()
        }
        embedding_batch_size = int(os.environ["KNOWLEDGE_EMBEDDING_BATCH_SIZE"])
        search_max_profiles = int(os.environ["KNOWLEDGE_SEARCH_MAX_PROFILES"])
        worker_heartbeat = int(os.environ["KNOWLEDGE_WORKER_HEARTBEAT_SECONDS"])
    except (KeyError, ValueError):
        raise SystemExit("knowledge deployment bounds invalid")
    embedding_timeout = finite_number("KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS")
    milvus_timeout = finite_number("MILVUS_TIMEOUT_SECONDS")
    if not (
        1 <= max_bases_per_user <= 1_000
        and 1 <= max_documents_per_base <= 10_000
        and 1 <= max_file_size <= 50 * 1024 * 1024
        and 100 <= chunk_size <= 16_000
        and 0 <= chunk_overlap
        and chunk_overlap * 2 <= chunk_size
        and chunk_size - chunk_overlap >= 100
        and 1 <= max_chunks_per_document <= 10_000
        and 1 <= parse_timeout <= 300
        and embedding_dimension > 1
        and embedding_dimension in embedding_allowed_dimensions
        and 1 <= embedding_batch_size <= 128
        and 1 <= embedding_timeout <= 120
        and 1 <= search_max_profiles <= 16
        and 0 < worker_poll <= 60
        and worker_lease > 0
        and 0 < worker_heartbeat * 2 < worker_lease
        and worker_max_attempts > 0
        and 0 < worker_retry_base <= worker_retry_max <= 3600
        and 1 <= milvus_timeout <= 60
    ):
        raise SystemExit("knowledge deployment bounds invalid")
    route_key = f'{os.environ["KNOWLEDGE_EMBEDDING_MODEL"]}@{os.environ["KNOWLEDGE_EMBEDDING_REVISION"]}'
    if not isinstance(routes.get(route_key), str):
        raise SystemExit("knowledge embedding revision route missing")
print(json.dumps(routes, separators=(",", ":")), end="")
PY
)" || exit 1
export KNOWLEDGE_EMBEDDING_REVISION_ROUTES="${validated_revision_routes}"
append_csv_value() {
  current="$1"
  required="$2"
  if [ -z "${current}" ]; then
    printf '%s' "${required}"
    return
  fi
  case ",${current}," in
    *",${required},"*) printf '%s' "${current}" ;;
    *) printf '%s,%s' "${current}" "${required}" ;;
  esac
}
export MCP_ALLOWED_HOSTS="$(append_csv_value "${MCP_ALLOWED_HOSTS}" "mcp.context7.com")"
export MCP_ALLOWED_CREDENTIAL_REFS="$(append_csv_value "${MCP_ALLOWED_CREDENTIAL_REFS}" "CONTEXT7_API_KEY")"
export FLYAI_API_KEY="${DEPLOY_FLYAI_API_KEY:-}"
if [ -z "${FLYAI_API_KEY}" ]; then
  echo "FlyAI 发布密钥未配置，拒绝以静默禁用方式部署"
  exit 1
fi
export FLYAI_ADAPTER_TOKEN="$(python3 -c 'import hashlib,hmac,os; print(hmac.new(os.environ["FLYAI_API_KEY"].encode(), b"fusion-deploy-flyai-adapter-v1", hashlib.sha256).hexdigest(), end="")')"
n=0
until docker pull "${DEPLOY_API_IMAGE}"; do
  n=$((n+1))
  if [ $n -ge 6 ]; then echo "docker pull 重试 $n 次仍失败"; exit 1; fi
  echo "docker pull 失败（网络抖动），第 $n 次，10s 后重试..."
  sleep 10
done
if [ "${KNOWLEDGE_BASE_ENABLED}" = "true" ]; then
  docker run --rm -i --entrypoint python --env-file "${FUSION_RUNTIME_ENV}" \
    -e "KNOWLEDGE_BASE_ENABLED" \
    -e "KNOWLEDGE_MAX_BASES_PER_USER" \
    -e "KNOWLEDGE_MAX_DOCUMENTS_PER_BASE" \
    -e "KNOWLEDGE_MAX_FILE_SIZE" \
    -e "KNOWLEDGE_ALLOWED_MIME_TYPES" \
    -e "KNOWLEDGE_PARSER_VERSION" \
    -e "KNOWLEDGE_CHUNKER_VERSION" \
    -e "KNOWLEDGE_CHUNK_SIZE" \
    -e "KNOWLEDGE_CHUNK_OVERLAP" \
    -e "KNOWLEDGE_MAX_CHUNKS_PER_DOCUMENT" \
    -e "KNOWLEDGE_PARSE_TIMEOUT_SECONDS" \
    -e "KNOWLEDGE_EMBEDDING_PROVIDER" \
    -e "KNOWLEDGE_EMBEDDING_MODEL" \
    -e "KNOWLEDGE_EMBEDDING_REVISION" \
    -e "KNOWLEDGE_EMBEDDING_REVISION_ROUTES" \
    -e "KNOWLEDGE_EMBEDDING_DIMENSION" \
    -e "KNOWLEDGE_EMBEDDING_ALLOWED_DIMENSIONS" \
    -e "KNOWLEDGE_EMBEDDING_BATCH_SIZE" \
    -e "KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS" \
    -e "KNOWLEDGE_SEARCH_MAX_PROFILES" \
    -e "KNOWLEDGE_DISTANCE_METRIC" \
    -e "KNOWLEDGE_WORKER_POLL_SECONDS" \
    -e "KNOWLEDGE_WORKER_LEASE_SECONDS" \
    -e "KNOWLEDGE_WORKER_HEARTBEAT_SECONDS" \
    -e "KNOWLEDGE_WORKER_MAX_ATTEMPTS" \
    -e "KNOWLEDGE_WORKER_RETRY_BASE_SECONDS" \
    -e "KNOWLEDGE_WORKER_RETRY_MAX_SECONDS" \
    -e "LITELLM_PROXY_URL" \
    -e "LITELLM_API_KEY" \
    -e "MILVUS_URI" \
    -e "MILVUS_USERNAME" \
    -e "MILVUS_PASSWORD" \
    -e "MILVUS_DATABASE" \
    -e "MILVUS_COLLECTION_PREFIX" \
    -e "MILVUS_TIMEOUT_SECONDS" \
    -e "FUSION_ROLLBACK_REQUESTED=${ROLLBACK_REQUESTED}" \
    "${DEPLOY_API_IMAGE}" - <<'PY'
import os

from app.core.config import settings

validator = getattr(settings, "validate_knowledge_base_configuration", None)
if validator is None:
    if os.environ.get("FUSION_ROLLBACK_REQUESTED") != "true":
        raise SystemExit("candidate knowledge settings validator missing")
    print("candidate knowledge settings skipped for legacy rollback image")
else:
    validator()
    print("candidate knowledge settings ok")
PY
fi
if docker run --rm --entrypoint /bin/sh "${DEPLOY_API_IMAGE}" -c 'test -f /app/scripts/run_knowledge_worker.py'; then
  knowledge_worker_supported="true"
else
  knowledge_worker_supported="false"
fi
printf '%s\n' "knowledge_worker_supported=${knowledge_worker_supported}" >> "${GITHUB_OUTPUT}"
n=0
until docker pull "${DEPLOY_ADAPTER_IMAGE}"; do
  n=$((n+1))
  if [ $n -ge 6 ]; then echo "FlyAI adapter 镜像拉取重试 $n 次仍失败"; exit 1; fi
  echo "FlyAI adapter 镜像拉取失败（网络抖动），第 $n 次，10s 后重试..."
  sleep 10
done
# dev 中 auth-service 只在 Docker 网络内暴露，未发布宿主机 8100。
# 显式覆盖旧 .env 里的宿主机地址，避免 fusion-api JWKS/userinfo 校验连到不可达地址。
export AUTH_SERVICE_INTERNAL_BASE_URL="http://auth-service:8100"
expected_governance_root="${HOME}/backups/litellm-governance"
governance_root="${LITELLM_GOVERNANCE_ROOT_HOST:-${expected_governance_root}}"
if [ "${governance_root}" != "${expected_governance_root}" ]; then
  echo "LITELLM_GOVERNANCE_ROOT_HOST 必须与 systemd 治理服务目录一致: ${expected_governance_root}"
  exit 1
fi
export LITELLM_GOVERNANCE_ROOT_HOST="${expected_governance_root}"
test -d "${LITELLM_GOVERNANCE_ROOT_HOST}"
docker network inspect "${MILVUS_DOCKER_NETWORK}" >/dev/null 2>&1 \
  || docker network create "${MILVUS_DOCKER_NETWORK}" >/dev/null
install -d -m 0755 "${FUSION_STORAGE_DIR}"
mkdir -p ./knowledge-worker-logs
if docker container inspect fusion-api >/dev/null 2>&1; then
  if docker inspect fusion-api --format '{{range .Mounts}}{{println .Destination}}{{end}}' | grep -qx '/app/storage/files'; then
    echo "file storage already mounted; skip container file migration"
  elif docker exec fusion-api sh -lc 'test -d /app/storage/files'; then
    docker exec fusion-api sh -lc 'tar -C /app/storage/files -cf - .' | tar -C "${FUSION_STORAGE_DIR}" -xf -
  fi
fi
cat > docker-compose.fusion-api-ghcr.yml <<'EOF'
services:
  flyai-adapter:
    image: ${DEPLOY_ADAPTER_IMAGE}
    container_name: fusion-flyai-adapter
    restart: always
    read_only: true
    tmpfs:
      - /tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    pids_limit: 64
    mem_limit: 256m
    cpus: 0.5
    environment:
      - FLYAI_API_KEY=${FLYAI_API_KEY}
      - FLYAI_ADAPTER_TOKEN=${FLYAI_ADAPTER_TOKEN}
      - PORT=8080
    networks:
      - fusion-flyai

  fusion-api:
    image: ${DEPLOY_API_IMAGE}
    container_name: fusion-api
    command: ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
    restart: always
    ports:
      - "8002:8000"
    volumes:
      - ./logs:/app/logs
      - ${FUSION_STORAGE_DIR}:/app/storage/files
      - ${LITELLM_GOVERNANCE_ROOT_HOST}:/var/lib/fusion/litellm-governance:ro
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - SERVER_HOST=${SERVER_HOST}
      - FRONTEND_URL=${FRONTEND_URL}
      - CORS_ORIGINS=${CORS_ORIGINS}
      - STORAGE_BACKEND=${STORAGE_BACKEND:-local}
      - FILE_STORAGE_PATH=/app/storage/files
      - FILE_UPLOAD_TIMEOUT_SECONDS=${FILE_UPLOAD_TIMEOUT_SECONDS:-60}
      - DIRECT_UPLOAD_STALE_SECONDS=${DIRECT_UPLOAD_STALE_SECONDS:-1800}
      - MINIO_ENDPOINT=${MINIO_ENDPOINT:-}
      - MINIO_ACCESS_KEY=${MINIO_ACCESS_KEY:-}
      - MINIO_SECRET_KEY=${MINIO_SECRET_KEY:-}
      - MINIO_BUCKET=${MINIO_BUCKET:-fusion-files}
      - MINIO_USE_SSL=${MINIO_USE_SSL:-false}
      - MINIO_PRESIGN_EXPIRES=${MINIO_PRESIGN_EXPIRES:-3600}
      - OSS_ENDPOINT=${OSS_ENDPOINT:-}
      - OSS_ACCESS_KEY_ID=${OSS_ACCESS_KEY_ID:-}
      - OSS_ACCESS_KEY_SECRET=${OSS_ACCESS_KEY_SECRET:-}
      - OSS_BUCKET=${OSS_BUCKET:-}
      - OSS_USE_SSL=${OSS_USE_SSL:-true}
      - AUTH_SERVICE_BASE_URL=${AUTH_SERVICE_BASE_URL}
      - AUTH_SERVICE_JWKS_URL=${AUTH_SERVICE_JWKS_URL}
      - AUTH_SERVICE_CLIENT_ID=${AUTH_SERVICE_CLIENT_ID}
      - AUTH_SERVICE_JWT_LEEWAY_SECONDS=${AUTH_SERVICE_JWT_LEEWAY_SECONDS:-5}
      - AUTH_SERVICE_INTERNAL_BASE_URL=${AUTH_SERVICE_INTERNAL_BASE_URL:-http://auth-service:8100}
      - CREDENTIAL_ENCRYPTION_KEY=${CREDENTIAL_ENCRYPTION_KEY}
      - GITHUB_CLIENT_ID=${GITHUB_CLIENT_ID}
      - GITHUB_CLIENT_SECRET=${GITHUB_CLIENT_SECRET}
      - GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
      - GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET}
      - REDIS_URL=${REDIS_URL:-redis://middleware-redis:6379/0}
      - MOONSHOT_API_KEY=${MOONSHOT_API_KEY:-}
      - LITELLM_PROXY_URL=${LITELLM_PROXY_URL:-http://litellm-proxy:4000}
      - LITELLM_API_KEY=${LITELLM_API_KEY:-}
      - RUN_CAPABILITY_CLASSIFIER_MODEL=${RUN_CAPABILITY_CLASSIFIER_MODEL:-deepseek-chat}
      - RUN_CAPABILITY_CLASSIFIER_TOKENIZER_MODEL=${RUN_CAPABILITY_CLASSIFIER_TOKENIZER_MODEL:-deepseek/deepseek-chat}
      # 全模型 /health 探测默认关闭（每模型真实 completion 产生费用），详见 docs/LITELLM_HEALTH.md
      - LITELLM_HEALTH_ENABLED=${LITELLM_HEALTH_ENABLED:-false}
      - LITELLM_GOVERNANCE_ROOT=/var/lib/fusion/litellm-governance
      - LITELLM_GOVERNANCE_MAX_AGE_SECONDS=${LITELLM_GOVERNANCE_MAX_AGE_SECONDS:-86400}
      - LITELLM_MODEL_MANAGEMENT_ENABLED=${LITELLM_MODEL_MANAGEMENT_ENABLED:-false}
      - LITELLM_MODEL_ADMISSION_WORKER_ENABLED=${LITELLM_MODEL_ADMISSION_WORKER_ENABLED:-false}
      - LITELLM_MODEL_ADMISSION_WORKER_TOKEN=${LITELLM_MODEL_ADMISSION_WORKER_TOKEN:-}
      - LITELLM_MODEL_ADMISSION_LEASE_SECONDS=${LITELLM_MODEL_ADMISSION_LEASE_SECONDS:-600}
      - PROMPTHUB_SYNC_MODE=${PROMPTHUB_SYNC_MODE:-disabled}
      - PROMPTHUB_BASE_URL=${PROMPTHUB_BASE_URL:-http://prompthub-backend:8000}
      - PROMPTHUB_API_KEY=${PROMPTHUB_API_KEY:-}
      - PROMPTHUB_PROJECT_SLUG=${PROMPTHUB_PROJECT_SLUG:-fusion}
      - PROMPTHUB_REQUEST_TIMEOUT_SECONDS=${PROMPTHUB_REQUEST_TIMEOUT_SECONDS:-3}
      - PROMPTHUB_SYNC_INTERVAL_SECONDS=${PROMPTHUB_SYNC_INTERVAL_SECONDS:-300}
      - PROMPTHUB_SYNC_ON_STARTUP=${PROMPTHUB_SYNC_ON_STARTUP:-true}
      - MCP_ALLOWED_HOSTS=${MCP_ALLOWED_HOSTS}
      - MCP_ALLOWED_CREDENTIAL_REFS=${MCP_ALLOWED_CREDENTIAL_REFS}
      - MCP_CONNECT_TIMEOUT_SECONDS=${MCP_CONNECT_TIMEOUT_SECONDS}
      - MCP_CALL_TIMEOUT_SECONDS=${MCP_CALL_TIMEOUT_SECONDS}
      - MCP_IDEMPOTENT_TOTAL_TIMEOUT_SECONDS=${MCP_IDEMPOTENT_TOTAL_TIMEOUT_SECONDS}
      - MCP_ADMIN_OPERATION_TIMEOUT_SECONDS=${MCP_ADMIN_OPERATION_TIMEOUT_SECONDS}
      - MCP_MAX_DISCOVERY_PAGES=${MCP_MAX_DISCOVERY_PAGES}
      - MCP_MAX_DISCOVERED_TOOLS=${MCP_MAX_DISCOVERED_TOOLS}
      - MCP_MAX_TOOL_DESCRIPTION_CHARS=${MCP_MAX_TOOL_DESCRIPTION_CHARS}
      - MCP_MAX_TOOL_SCHEMA_BYTES=${MCP_MAX_TOOL_SCHEMA_BYTES}
      - MCP_MAX_RESPONSE_BYTES=${MCP_MAX_RESPONSE_BYTES}
      - MCP_MAX_TOOL_CALLS_PER_SERVER_PER_RUN=${MCP_MAX_TOOL_CALLS_PER_SERVER_PER_RUN}
      - MCP_SERVER_CIRCUIT_FAILURE_THRESHOLD=${MCP_SERVER_CIRCUIT_FAILURE_THRESHOLD}
      - MCP_SERVER_CIRCUIT_COOLDOWN_SECONDS=${MCP_SERVER_CIRCUIT_COOLDOWN_SECONDS}
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY:-}
      - AMAP_MCP_API_KEY=${AMAP_MCP_API_KEY:-}
      - CONTEXT7_API_KEY=${CONTEXT7_API_KEY:-}
      - ENABLE_FLYAI_TRAVEL_TOOLS=true
      - FLYAI_ADAPTER_BASE_URL=http://flyai-adapter:8080
      - FLYAI_ADAPTER_TOKEN=${FLYAI_ADAPTER_TOKEN}
      - FLYAI_TRAVEL_TOOL_TIMEOUT_SECONDS=20
      - FLYAI_TRAVEL_MAX_TOOL_CALLS_PER_RUN=4
      - KNOWLEDGE_BASE_ENABLED=${KNOWLEDGE_BASE_ENABLED}
      - KNOWLEDGE_MAX_BASES_PER_USER=${KNOWLEDGE_MAX_BASES_PER_USER}
      - KNOWLEDGE_MAX_DOCUMENTS_PER_BASE=${KNOWLEDGE_MAX_DOCUMENTS_PER_BASE}
      - KNOWLEDGE_MAX_FILE_SIZE=${KNOWLEDGE_MAX_FILE_SIZE}
      - KNOWLEDGE_ALLOWED_MIME_TYPES=${KNOWLEDGE_ALLOWED_MIME_TYPES}
      - KNOWLEDGE_PARSER_VERSION=${KNOWLEDGE_PARSER_VERSION}
      - KNOWLEDGE_CHUNKER_VERSION=${KNOWLEDGE_CHUNKER_VERSION}
      - KNOWLEDGE_EMBEDDING_PROVIDER=${KNOWLEDGE_EMBEDDING_PROVIDER}
      - KNOWLEDGE_EMBEDDING_MODEL=${KNOWLEDGE_EMBEDDING_MODEL}
      - KNOWLEDGE_EMBEDDING_REVISION=${KNOWLEDGE_EMBEDDING_REVISION}
      - KNOWLEDGE_EMBEDDING_REVISION_ROUTES=${KNOWLEDGE_EMBEDDING_REVISION_ROUTES}
      - KNOWLEDGE_EMBEDDING_DIMENSION=${KNOWLEDGE_EMBEDDING_DIMENSION}
      - KNOWLEDGE_EMBEDDING_ALLOWED_DIMENSIONS=${KNOWLEDGE_EMBEDDING_ALLOWED_DIMENSIONS}
      - KNOWLEDGE_CHUNK_SIZE=${KNOWLEDGE_CHUNK_SIZE}
      - KNOWLEDGE_CHUNK_OVERLAP=${KNOWLEDGE_CHUNK_OVERLAP}
      - KNOWLEDGE_MAX_CHUNKS_PER_DOCUMENT=${KNOWLEDGE_MAX_CHUNKS_PER_DOCUMENT}
      - KNOWLEDGE_PARSE_TIMEOUT_SECONDS=${KNOWLEDGE_PARSE_TIMEOUT_SECONDS}
      - KNOWLEDGE_EMBEDDING_BATCH_SIZE=${KNOWLEDGE_EMBEDDING_BATCH_SIZE}
      - KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS=${KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS}
      - KNOWLEDGE_SEARCH_MAX_PROFILES=${KNOWLEDGE_SEARCH_MAX_PROFILES}
      - KNOWLEDGE_DISTANCE_METRIC=${KNOWLEDGE_DISTANCE_METRIC}
      - KNOWLEDGE_WORKER_POLL_SECONDS=${KNOWLEDGE_WORKER_POLL_SECONDS}
      - KNOWLEDGE_WORKER_LEASE_SECONDS=${KNOWLEDGE_WORKER_LEASE_SECONDS}
      - KNOWLEDGE_WORKER_HEARTBEAT_SECONDS=${KNOWLEDGE_WORKER_HEARTBEAT_SECONDS}
      - KNOWLEDGE_WORKER_MAX_ATTEMPTS=${KNOWLEDGE_WORKER_MAX_ATTEMPTS}
      - KNOWLEDGE_WORKER_RETRY_BASE_SECONDS=${KNOWLEDGE_WORKER_RETRY_BASE_SECONDS}
      - KNOWLEDGE_WORKER_RETRY_MAX_SECONDS=${KNOWLEDGE_WORKER_RETRY_MAX_SECONDS}
      - KNOWLEDGE_WORKER_HEALTH_FILE=${KNOWLEDGE_WORKER_HEALTH_FILE}
      - MILVUS_URI=${MILVUS_URI}
      - MILVUS_USERNAME=${MILVUS_USERNAME}
      - MILVUS_PASSWORD=${MILVUS_PASSWORD}
      - MILVUS_DATABASE=${MILVUS_DATABASE}
      - MILVUS_COLLECTION_PREFIX=${MILVUS_COLLECTION_PREFIX}
      - MILVUS_TIMEOUT_SECONDS=${MILVUS_TIMEOUT_SECONDS}
      - MILVUS_DOCKER_NETWORK=${MILVUS_DOCKER_NETWORK}
      - ENABLE_DOCS=false
    deploy:
      resources:
        limits:
          cpus: "1"
          memory: 1g
    networks:
      - postgres_default
      - middleware_default
      - fusion-prompthub
      - fusion-flyai
      - fusion-knowledge-milvus

  knowledge-worker:
    profiles: ["knowledge-worker"]
    image: ${DEPLOY_API_IMAGE}
    container_name: fusion-knowledge-worker
    command: ["python", "-m", "scripts.run_knowledge_worker"]
    restart: always
    volumes:
      - ./knowledge-worker-logs:/app/logs
      - ${FUSION_STORAGE_DIR}:/app/storage/files
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - STORAGE_BACKEND=${STORAGE_BACKEND:-local}
      - FILE_STORAGE_PATH=/app/storage/files
      - MINIO_ENDPOINT=${MINIO_ENDPOINT:-}
      - MINIO_ACCESS_KEY=${MINIO_ACCESS_KEY:-}
      - MINIO_SECRET_KEY=${MINIO_SECRET_KEY:-}
      - MINIO_BUCKET=${MINIO_BUCKET:-fusion-files}
      - MINIO_USE_SSL=${MINIO_USE_SSL:-false}
      - OSS_ENDPOINT=${OSS_ENDPOINT:-}
      - OSS_ACCESS_KEY_ID=${OSS_ACCESS_KEY_ID:-}
      - OSS_ACCESS_KEY_SECRET=${OSS_ACCESS_KEY_SECRET:-}
      - OSS_BUCKET=${OSS_BUCKET:-}
      - OSS_USE_SSL=${OSS_USE_SSL:-true}
      - LITELLM_PROXY_URL=${LITELLM_PROXY_URL:-http://litellm-proxy:4000}
      - LITELLM_API_KEY=${LITELLM_API_KEY:-}
      - KNOWLEDGE_BASE_ENABLED=${KNOWLEDGE_BASE_ENABLED}
      - KNOWLEDGE_MAX_BASES_PER_USER=${KNOWLEDGE_MAX_BASES_PER_USER}
      - KNOWLEDGE_MAX_DOCUMENTS_PER_BASE=${KNOWLEDGE_MAX_DOCUMENTS_PER_BASE}
      - KNOWLEDGE_MAX_FILE_SIZE=${KNOWLEDGE_MAX_FILE_SIZE}
      - KNOWLEDGE_ALLOWED_MIME_TYPES=${KNOWLEDGE_ALLOWED_MIME_TYPES}
      - KNOWLEDGE_PARSER_VERSION=${KNOWLEDGE_PARSER_VERSION}
      - KNOWLEDGE_CHUNKER_VERSION=${KNOWLEDGE_CHUNKER_VERSION}
      - KNOWLEDGE_EMBEDDING_PROVIDER=${KNOWLEDGE_EMBEDDING_PROVIDER}
      - KNOWLEDGE_EMBEDDING_MODEL=${KNOWLEDGE_EMBEDDING_MODEL}
      - KNOWLEDGE_EMBEDDING_REVISION=${KNOWLEDGE_EMBEDDING_REVISION}
      - KNOWLEDGE_EMBEDDING_REVISION_ROUTES=${KNOWLEDGE_EMBEDDING_REVISION_ROUTES}
      - KNOWLEDGE_EMBEDDING_DIMENSION=${KNOWLEDGE_EMBEDDING_DIMENSION}
      - KNOWLEDGE_EMBEDDING_ALLOWED_DIMENSIONS=${KNOWLEDGE_EMBEDDING_ALLOWED_DIMENSIONS}
      - KNOWLEDGE_CHUNK_SIZE=${KNOWLEDGE_CHUNK_SIZE}
      - KNOWLEDGE_CHUNK_OVERLAP=${KNOWLEDGE_CHUNK_OVERLAP}
      - KNOWLEDGE_MAX_CHUNKS_PER_DOCUMENT=${KNOWLEDGE_MAX_CHUNKS_PER_DOCUMENT}
      - KNOWLEDGE_PARSE_TIMEOUT_SECONDS=${KNOWLEDGE_PARSE_TIMEOUT_SECONDS}
      - KNOWLEDGE_EMBEDDING_BATCH_SIZE=${KNOWLEDGE_EMBEDDING_BATCH_SIZE}
      - KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS=${KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS}
      - KNOWLEDGE_SEARCH_MAX_PROFILES=${KNOWLEDGE_SEARCH_MAX_PROFILES}
      - KNOWLEDGE_DISTANCE_METRIC=${KNOWLEDGE_DISTANCE_METRIC}
      - KNOWLEDGE_WORKER_POLL_SECONDS=${KNOWLEDGE_WORKER_POLL_SECONDS}
      - KNOWLEDGE_WORKER_LEASE_SECONDS=${KNOWLEDGE_WORKER_LEASE_SECONDS}
      - KNOWLEDGE_WORKER_HEARTBEAT_SECONDS=${KNOWLEDGE_WORKER_HEARTBEAT_SECONDS}
      - KNOWLEDGE_WORKER_MAX_ATTEMPTS=${KNOWLEDGE_WORKER_MAX_ATTEMPTS}
      - KNOWLEDGE_WORKER_RETRY_BASE_SECONDS=${KNOWLEDGE_WORKER_RETRY_BASE_SECONDS}
      - KNOWLEDGE_WORKER_RETRY_MAX_SECONDS=${KNOWLEDGE_WORKER_RETRY_MAX_SECONDS}
      - KNOWLEDGE_WORKER_HEALTH_FILE=${KNOWLEDGE_WORKER_HEALTH_FILE}
      - MILVUS_URI=${MILVUS_URI}
      - MILVUS_USERNAME=${MILVUS_USERNAME}
      - MILVUS_PASSWORD=${MILVUS_PASSWORD}
      - MILVUS_DATABASE=${MILVUS_DATABASE}
      - MILVUS_COLLECTION_PREFIX=${MILVUS_COLLECTION_PREFIX}
      - MILVUS_TIMEOUT_SECONDS=${MILVUS_TIMEOUT_SECONDS}
      - MILVUS_DOCKER_NETWORK=${MILVUS_DOCKER_NETWORK}
    healthcheck:
      test: ["CMD", "python", "-m", "scripts.run_knowledge_worker", "--healthcheck", "--health-max-age-seconds", "120"]
      interval: 30s
      timeout: 10s
      retries: 4
      start_period: 30s
    deploy:
      resources:
        limits:
          cpus: "1"
          memory: 1g
    networks:
      - postgres_default
      - middleware_default
      - fusion-knowledge-milvus

networks:
  postgres_default:
    external: true
  middleware_default:
    external: true
  fusion-prompthub:
    external: true
  fusion-flyai:
    name: fusion-flyai
    driver: bridge
  fusion-knowledge-milvus:
    external: true
    name: ${MILVUS_DOCKER_NETWORK}
EOF
docker network inspect fusion-prompthub >/dev/null 2>&1 \
  || docker network create fusion-prompthub >/dev/null
if [ "${knowledge_worker_supported}" = "true" ]; then
  docker compose --project-name fusion -f docker-compose.fusion-api-ghcr.yml --profile knowledge-worker up -d
else
  docker compose --project-name fusion -f docker-compose.fusion-api-ghcr.yml up -d
  docker rm -f fusion-knowledge-worker >/dev/null 2>&1 || true
fi
