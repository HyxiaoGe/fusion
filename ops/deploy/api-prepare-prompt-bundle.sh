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
if [ "${DEPLOY_PROMPTHUB_SYNC_MODE:-disabled}" != "apply" ]; then
  printf '当前不是 apply 模式，无需预置 v2 LKG\n'
  exit 0
fi
case "${DEPLOY_API_IMAGE}" in
  *@sha256:*) ;;
  *) printf 'v2 预置必须绑定候选镜像 digest\n' >&2; exit 1 ;;
esac
if [ "${DEPLOY_PROMPT_P0_BASELINE_ATTESTED:-false}" != "true" ]; then
  printf 'v2 预置要求已完成 P0 基线 attestation\n' >&2
  exit 1
fi

set -a
# runtime.env 路径由前置受管路径检查提供，内容须按 shell 语义读取。
# shellcheck disable=SC1090
source "${FUSION_RUNTIME_ENV}"
set +a
export PROMPTHUB_API_KEY="${DEPLOY_PROMPTHUB_API_KEY:-}"
export PROMPTHUB_SYNC_MODE="${DEPLOY_PROMPTHUB_SYNC_MODE}"
export PROMPT_P0_BASELINE_ATTESTED="${DEPLOY_PROMPT_P0_BASELINE_ATTESTED}"
export PROMPTHUB_PROJECT_SLUG="${PROMPTHUB_PROJECT_SLUG:-fusion}"
export PROMPTHUB_REQUEST_TIMEOUT_SECONDS="${PROMPTHUB_REQUEST_TIMEOUT_SECONDS:-3}"
test -n "${PROMPTHUB_API_KEY}"
docker network inspect postgres_default >/dev/null
docker network inspect fusion-prompthub >/dev/null

umask 077
bridge_dir="$(mktemp -d)"
bridge_container=""
baseline_container=""
cleanup_bridge() {
  if [ -n "${baseline_container}" ]; then
    docker rm -f "${baseline_container}" >/dev/null 2>&1 || true
  fi
  if [ -n "${bridge_container}" ]; then
    docker rm -f "${bridge_container}" >/dev/null 2>&1 || true
  fi
  rm -rf "${bridge_dir}"
}
trap cleanup_bridge EXIT

# 使用核验过的旧镜像和原容器配置；停止或反复重启的服务也可读取原 LKG。
cat > "${bridge_dir}/capture-config.py" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

container, = json.load(sys.stdin)
expected_id = os.environ["PREVIOUS_API_ID"]
if (
    re.fullmatch(r"sha256:[0-9a-f]{64}", expected_id) is None
    or container["Image"] != expected_id
    or container["Config"]["Image"] != os.environ["PREVIOUS_API_REF"]
):
    raise SystemExit("旧容器镜像身份已变化，拒绝抓取不一致基线")
allowed = {"DATABASE_URL", "PROMPTHUB_PROJECT_SLUG", "PROMPTHUB_SYNC_MODE", "PROMPT_P0_BASELINE_ATTESTED"}
captured = {}
for entry in container["Config"].get("Env") or []:
    key, separator, value = entry.partition("=")
    if separator and key in allowed:
        if key in captured or "\n" in value or "\r" in value:
            raise SystemExit("原 Prompt 配置重复或含多行，无法精确传递")
        captured[key] = value
if not captured.get("DATABASE_URL"):
    raise SystemExit("原容器缺少数据库配置，拒绝抓取基线")
Path(sys.argv[1]).write_text("".join(f"{key}={value}\n" for key, value in captured.items()), encoding="utf-8")
PY
docker inspect fusion-api | python3 "${bridge_dir}/capture-config.py" "${bridge_dir}/baseline.env"

baseline_code="$(cat <<'PY'
import hashlib
import json

from app.ai.prompts.defaults import DEFAULT_PROMPT_TEMPLATES
from app.core.config import settings
from app.core.prompt_bundle import freeze_prompt_bundle

snapshot = freeze_prompt_bundle(DEFAULT_PROMPT_TEMPLATES)
if snapshot.source_kind != "prompthub_lkg":
    raise SystemExit("部署前不是完整 LKG 来源，拒绝无证据桥接")
print(json.dumps({
    "project_slug": settings.PROMPTHUB_PROJECT_SLUG,
    "source_kind": snapshot.source_kind,
    "revision": snapshot.source_revision,
    "prompts": {
        item.key: {"content": item.content, "content_sha256": hashlib.sha256(item.content.encode("utf-8")).hexdigest()}
        for item in snapshot.templates
    },
}, ensure_ascii=False))
PY
)"
baseline_container="$(docker create --network postgres_default \
  --env-file "${bridge_dir}/baseline.env" --entrypoint python \
  "${PREVIOUS_API_ID}" -c "${baseline_code}")"
docker start --attach "${baseline_container}" > "${bridge_dir}/baseline.json"
test "$(docker inspect --format '{{.State.ExitCode}}' "${baseline_container}")" = "0"

# 新容器只运行预置命令，不启动 lifespan；新旧同步器的存储键互相隔离。
bridge_container="$(docker create \
  --network postgres_default \
  -e DATABASE_URL -e PROMPTHUB_PROJECT_SLUG -e PROMPTHUB_REQUEST_TIMEOUT_SECONDS \
  -e PROMPTHUB_API_KEY -e PROMPTHUB_SYNC_MODE -e PROMPT_P0_BASELINE_ATTESTED \
  -e "PROMPTHUB_BASE_URL=${PROMPTHUB_BASE_URL:-http://prompthub-backend:8000}" \
  -v "${bridge_dir}:/prompt-bridge:ro" \
  --entrypoint python \
  "${DEPLOY_API_IMAGE}" scripts/bridge_prompt_bundle_v2.py prepare \
  --baseline /prompt-bridge/baseline.json \
  --actor "github-actions:${GITHUB_RUN_ID}" \
  --reason '候选服务启动前预置完整 v2 LKG' --apply)"
docker network connect fusion-prompthub "${bridge_container}"
docker start --attach "${bridge_container}"
bridge_exit="$(docker inspect --format '{{.State.ExitCode}}' "${bridge_container}")"
test "${bridge_exit}" = "0"
printf '完整 v2 LKG 已通过部署前桥接检查；旧 v1 active 保持不变\n'
