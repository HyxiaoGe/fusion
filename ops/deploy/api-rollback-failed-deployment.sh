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
systemctl --user stop fusion-litellm-model-management.timer >/dev/null 2>&1 || true
systemctl --user stop fusion-litellm-governance.timer >/dev/null 2>&1 || true
systemctl --user stop fusion-litellm-cost-sync.timer >/dev/null 2>&1 || true
wait_for_user_services() {
  for attempt in $(seq 1 24); do
    worker_active="false"
    governance_active="false"
    cost_active="false"
    if systemctl --user is-active --quiet fusion-litellm-model-management.service; then
      worker_active="true"
    fi
    if systemctl --user is-active --quiet fusion-litellm-governance.service; then
      governance_active="true"
    fi
    if systemctl --user is-active --quiet fusion-litellm-cost-sync.service; then
      cost_active="true"
    fi
    if [ "${worker_active}:${governance_active}:${cost_active}" = "false:false:false" ]; then
      return 0
    fi
    sleep 5
  done
  echo "模型准入 Worker、LiteLLM 治理或成本同步周期在回滚等待窗口内仍未结束，拒绝并发恢复运行资产"
  return 1
}
wait_for_user_services
cd "${FUSION_API_RUNTIME_DIR}"
set -o pipefail
set -a
source "${FUSION_RUNTIME_ENV}"
set +a
export ROLLBACK_KNOWLEDGE_CONFIG_FILE="${RUNNER_TEMP}/fusion-knowledge-rollback-${GITHUB_RUN_ID}-${GITHUB_JOB}-${GITHUB_RUN_ATTEMPT}.env"
case "${ROLLBACK_KNOWLEDGE_CONFIG_FILE}" in
  "${RUNNER_TEMP}"/fusion-knowledge-rollback-*.env) ;;
  *) echo "知识库回滚配置未落在 RUNNER_TEMP 的受管路径中"; exit 1 ;;
esac
if [ ! -f "${ROLLBACK_KNOWLEDGE_CONFIG_FILE}" ] || [ -L "${ROLLBACK_KNOWLEDGE_CONFIG_FILE}" ]; then
  echo "知识库回滚配置快照缺失或不是受管普通文件"
  exit 1
fi
if [ "$(stat -c '%a' "${ROLLBACK_KNOWLEDGE_CONFIG_FILE}")" != "600" ]; then
  echo "知识库回滚配置快照权限无效"
  exit 1
fi
source "${ROLLBACK_KNOWLEDGE_CONFIG_FILE}"
compact_revision_routes="$(python3 -c '
import json
import os
import re

routes = json.loads(os.environ.get("KNOWLEDGE_EMBEDDING_REVISION_ROUTES", "{}"))
if not isinstance(routes, dict):
    raise SystemExit("rollback knowledge embedding revision routes invalid")
for key, alias in routes.items():
    if not isinstance(key, str) or not isinstance(alias, str):
        raise SystemExit("rollback knowledge embedding revision routes invalid")
    model, separator, revision = key.rpartition("@")
    if (
        not separator
        or re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", model) is None
        or re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", revision) is None
        or re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", alias) is None
        or alias.startswith("litellm_proxy/")
    ):
        raise SystemExit("rollback knowledge embedding revision routes invalid")
print(json.dumps(routes, separators=(",", ":")), end="")
')" || exit 1
export KNOWLEDGE_EMBEDDING_REVISION_ROUTES="${compact_revision_routes}"
export CONTEXT7_API_KEY=""
export PROMPTHUB_API_KEY="${DEPLOY_PROMPTHUB_API_KEY:-}"
export PROMPTHUB_SYNC_MODE="${DEPLOY_PROMPTHUB_SYNC_MODE:-disabled}"
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
export LITELLM_MODEL_MANAGEMENT_ENABLED="$(printf '%s' "${ROLLBACK_LITELLM_MODEL_MANAGEMENT_ENABLED}" | tr '[:upper:]' '[:lower:]')"
export LITELLM_MODEL_ADMISSION_WORKER_ENABLED="$(printf '%s' "${ROLLBACK_LITELLM_MODEL_ADMISSION_WORKER_ENABLED}" | tr '[:upper:]' '[:lower:]')"
export LITELLM_GOVERNANCE_MAX_AGE_SECONDS="${DEPLOY_LITELLM_GOVERNANCE_MAX_AGE_SECONDS:-86400}"
export LITELLM_MODEL_ADMISSION_WORKER_TOKEN="${DEPLOY_LITELLM_MODEL_ADMISSION_WORKER_TOKEN:-}"
case "${LITELLM_GOVERNANCE_MAX_AGE_SECONDS}" in
  ''|*[!0-9]*|0) echo "治理快照最大年龄必须是正整数"; exit 1 ;;
esac
case "${LITELLM_MODEL_MANAGEMENT_ENABLED}:${LITELLM_MODEL_ADMISSION_WORKER_ENABLED}" in
  true:true|true:false|false:false) ;;
  *) echo "模型管理开关必须为 true 或 false，且 Worker 不能单独启用"; exit 1 ;;
esac
if [ "${LITELLM_MODEL_ADMISSION_WORKER_ENABLED}" = "true" ] && [ -z "${LITELLM_MODEL_ADMISSION_WORKER_TOKEN}" ]; then
  echo "模型准入 Worker 已启用，但回滚发布密钥未配置"
  exit 1
fi
case "${KNOWLEDGE_BASE_ENABLED}" in
  true|false) ;;
  *) echo "回滚知识库开关必须为 true 或 false"; exit 1 ;;
esac
case "${MILVUS_DOCKER_NETWORK}" in
  ''|*[!a-zA-Z0-9_.-]*) echo "回滚 Milvus Docker 网络格式无效"; exit 1 ;;
esac
if [ "${KNOWLEDGE_BASE_ENABLED}" = "true" ]; then
  if [ -z "${KNOWLEDGE_EMBEDDING_MODEL}" ] \
    || [ -z "${KNOWLEDGE_EMBEDDING_REVISION}" ] \
    || [ -z "${MILVUS_URI}" ] \
    || [ -z "${MILVUS_USERNAME}" ] \
    || [ -z "${MILVUS_PASSWORD}" ] \
    || [ -z "${MILVUS_DATABASE}" ]; then
    echo "回滚知识库配置快照不完整"
    exit 1
  fi
  if [ "$(printf '%s' "${MILVUS_USERNAME}" | tr '[:upper:]' '[:lower:]')" = "root" ]; then
    echo "回滚知识库禁止使用 Milvus root 账号"
    exit 1
  fi
fi
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
  echo "FlyAI 发布密钥未配置，无法恢复原部署"
  exit 1
fi
export FLYAI_ADAPTER_TOKEN="$(python3 -c 'import hashlib,hmac,os; print(hmac.new(os.environ["FLYAI_API_KEY"].encode(), b"fusion-deploy-flyai-adapter-v1", hashlib.sha256).hexdigest(), end="")')"
export AUTH_SERVICE_INTERNAL_BASE_URL="http://auth-service:8100"
expected_governance_root="${HOME}/backups/litellm-governance"
governance_root="${LITELLM_GOVERNANCE_ROOT_HOST:-${expected_governance_root}}"
if [ "${governance_root}" != "${expected_governance_root}" ]; then
  echo "LITELLM_GOVERNANCE_ROOT_HOST 必须与 systemd 治理服务目录一致: ${expected_governance_root}"
  exit 1
fi
export LITELLM_GOVERNANCE_ROOT_HOST="${expected_governance_root}"
export DEPLOY_API_IMAGE="${ROLLBACK_API_IMAGE_REF}"
export DEPLOY_ADAPTER_IMAGE="${ROLLBACK_ADAPTER_IMAGE_REF}"

ensure_rollback_image() {
  image_ref="$1"
  expected_id="$2"
  actual_id="$(docker image inspect "${image_ref}" --format '{{.Id}}' 2>/dev/null || true)"
  if [ "${actual_id}" != "${expected_id}" ]; then
    docker pull "${image_ref}"
    actual_id="$(docker image inspect "${image_ref}" --format '{{.Id}}')"
  fi
  if [ -z "${expected_id}" ] || [ "${actual_id}" != "${expected_id}" ]; then
    echo "回滚镜像内容 ID 不匹配: ref=${image_ref} expected=${expected_id} actual=${actual_id:-<empty>}"
    exit 1
  fi
}

ensure_rollback_image "${ROLLBACK_API_IMAGE_REF}" "${ROLLBACK_API_IMAGE_ID}"
ensure_rollback_image "${ROLLBACK_ADAPTER_IMAGE_REF}" "${ROLLBACK_ADAPTER_IMAGE_ID}"

current_api_ref="$(docker inspect fusion-api --format '{{.Config.Image}}' 2>/dev/null || true)"
current_api_id="$(docker inspect fusion-api --format '{{.Image}}' 2>/dev/null || true)"
current_adapter_ref="$(docker inspect fusion-flyai-adapter --format '{{.Config.Image}}' 2>/dev/null || true)"
current_adapter_id="$(docker inspect fusion-flyai-adapter --format '{{.Image}}' 2>/dev/null || true)"
current_worker_ref="$(docker inspect fusion-knowledge-worker --format '{{.Config.Image}}' 2>/dev/null || true)"
current_worker_id="$(docker inspect fusion-knowledge-worker --format '{{.Image}}' 2>/dev/null || true)"
worker_requires_restore="false"
case "${ROLLBACK_KNOWLEDGE_WORKER_EXISTED}" in
  true)
    if [ "${current_worker_ref}" != "${ROLLBACK_KNOWLEDGE_WORKER_IMAGE_REF}" ] \
      || [ "${current_worker_id}" != "${ROLLBACK_KNOWLEDGE_WORKER_IMAGE_ID}" ]; then
      worker_requires_restore="true"
    fi
    ;;
  false)
    if [ -n "${current_worker_ref}" ] || [ -n "${current_worker_id}" ]; then
      worker_requires_restore="true"
    fi
    ;;
  *) echo "知识库 Worker 回滚状态无效"; exit 1 ;;
esac
if [ "${current_api_ref}" != "${ROLLBACK_API_IMAGE_REF}" ] \
  || [ "${current_api_id}" != "${ROLLBACK_API_IMAGE_ID}" ] \
  || [ "${current_adapter_ref}" != "${ROLLBACK_ADAPTER_IMAGE_REF}" ] \
  || [ "${current_adapter_id}" != "${ROLLBACK_ADAPTER_IMAGE_ID}" ] \
  || [ "${worker_requires_restore}" = "true" ]; then
  if [ ! -f docker-compose.fusion-api-ghcr.yml ]; then
    echo "候选部署未生成 compose 文件且容器身份已变化，无法安全回滚"
    exit 1
  fi
  if [ "${ROLLBACK_KNOWLEDGE_WORKER_EXISTED}" = "true" ]; then
    docker compose --project-name fusion -f docker-compose.fusion-api-ghcr.yml --profile knowledge-worker up -d
  else
    docker compose --project-name fusion -f docker-compose.fusion-api-ghcr.yml up -d
    docker rm -f fusion-knowledge-worker >/dev/null 2>&1 || true
  fi
else
  echo "候选失败发生在容器变更前，原部署无需重建"
fi

restored_api_ref="$(docker inspect fusion-api --format '{{.Config.Image}}')"
restored_api_id="$(docker inspect fusion-api --format '{{.Image}}')"
restored_adapter_ref="$(docker inspect fusion-flyai-adapter --format '{{.Config.Image}}')"
restored_adapter_id="$(docker inspect fusion-flyai-adapter --format '{{.Image}}')"
if [ "${restored_api_ref}" != "${ROLLBACK_API_IMAGE_REF}" ] \
  || [ "${restored_api_id}" != "${ROLLBACK_API_IMAGE_ID}" ]; then
  echo "fusion-api 回滚身份校验失败"
  exit 1
fi
if [ "${restored_adapter_ref}" != "${ROLLBACK_ADAPTER_IMAGE_REF}" ] \
  || [ "${restored_adapter_id}" != "${ROLLBACK_ADAPTER_IMAGE_ID}" ]; then
  echo "fusion-flyai-adapter 回滚身份校验失败"
  exit 1
fi
if [ "${ROLLBACK_KNOWLEDGE_WORKER_EXISTED}" = "true" ]; then
  restored_worker_ref="$(docker inspect fusion-knowledge-worker --format '{{.Config.Image}}')"
  restored_worker_id="$(docker inspect fusion-knowledge-worker --format '{{.Image}}')"
  if [ "${restored_worker_ref}" != "${ROLLBACK_KNOWLEDGE_WORKER_IMAGE_REF}" ] \
    || [ "${restored_worker_id}" != "${ROLLBACK_KNOWLEDGE_WORKER_IMAGE_ID}" ]; then
    echo "fusion-knowledge-worker 回滚身份校验失败"
    exit 1
  fi
elif docker container inspect fusion-knowledge-worker >/dev/null 2>&1; then
  echo "fusion-knowledge-worker 回滚清理失败"
  exit 1
fi

api_healthy=0
for attempt in $(seq 1 30); do
  if docker exec fusion-api python -c \
    "import json,urllib.request; body=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)); raise SystemExit(0 if body.get('status') == 'healthy' else 1)"; then
    api_healthy=1
    break
  fi
  sleep 2
done
if [ "${api_healthy}" != "1" ]; then
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
if [ "${adapter_healthy}" != "1" ]; then
  docker logs --tail=120 fusion-flyai-adapter || true
  exit 1
fi
if [ "${ROLLBACK_KNOWLEDGE_WORKER_EXISTED}" = "true" ]; then
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
fi

rollback_api_sha="${ROLLBACK_DEPLOYMENT_SHA}"
if [[ ! "${rollback_api_sha}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "fusion-api 回滚发布 SHA 不是 40 位小写 Git SHA: ${rollback_api_sha}"
  exit 1
fi
if ! git -C "${GITHUB_WORKSPACE}" cat-file -e "${rollback_api_sha}^{commit}"; then
  echo "fusion-api 回滚提交不在当前仓库历史中: ${rollback_api_sha}"
  exit 1
fi
rollback_smoke_path=""
for rollback_smoke_path in \
  backend/scripts/deployment_smoke.py \
  scripts/deployment_smoke.py; do
  if git -C "${GITHUB_WORKSPACE}" cat-file -e \
    "${rollback_api_sha}:${rollback_smoke_path}" 2>/dev/null; then
    break
  fi
  rollback_smoke_path=""
done
if [ -z "${rollback_smoke_path}" ]; then
  echo "fusion-api 回滚提交不含受支持的 deployment smoke: ${rollback_api_sha}"
  exit 1
fi
if ! git -C "${GITHUB_WORKSPACE}" show "${rollback_api_sha}:${rollback_smoke_path}" \
  | python3 - --base-url http://127.0.0.1:8002; then
  echo "fusion-api rollback deployment smoke failed"
  docker logs --tail=120 fusion-api || true
  exit 1
fi

echo "Restore LiteLLM governance discovery after automatic rollback"
governance_current_link="${HOME}/.local/share/fusion/litellm-governance-current"
systemctl --user stop fusion-litellm-governance.timer >/dev/null 2>&1 || true
if systemctl --user is-active --quiet fusion-litellm-governance.service; then
  echo "LiteLLM 治理周期仍在执行，无法恢复回滚前版本"
  exit 1
fi
case "${ROLLBACK_GOVERNANCE_TIMER_ENABLED}:${ROLLBACK_GOVERNANCE_TIMER_ACTIVE}" in
  true:true|true:false|false:true|false:false) ;;
  *) echo "LiteLLM 治理定时器回滚状态无效"; exit 1 ;;
esac
if [ -n "${ROLLBACK_GOVERNANCE_CURRENT_TARGET}" ]; then
  case "${ROLLBACK_GOVERNANCE_CURRENT_TARGET}" in
    "${HOME}/.local/share/fusion/litellm-governance-src-"*) ;;
    *) echo "LiteLLM 治理回滚版本不在受管目录中"; exit 1 ;;
  esac
  if [ ! -d "${ROLLBACK_GOVERNANCE_CURRENT_TARGET}" ]; then
    echo "LiteLLM 治理回滚版本不存在: ${ROLLBACK_GOVERNANCE_CURRENT_TARGET}"
    exit 1
  fi
  if [ -e "${governance_current_link}" ] && [ ! -L "${governance_current_link}" ]; then
    echo "LiteLLM 治理当前版本路径不是符号链接，无法恢复"
    exit 1
  fi
  ln -sfn "${ROLLBACK_GOVERNANCE_CURRENT_TARGET}" "${governance_current_link}"
elif [ -L "${governance_current_link}" ]; then
  unlink "${governance_current_link}"
elif [ -e "${governance_current_link}" ]; then
  echo "LiteLLM 治理当前版本路径不是符号链接，无法清理"
  exit 1
fi
governance_unit_dir="${HOME}/.config/systemd/user"
install -d -m 0755 "${governance_unit_dir}"
restore_governance_unit() {
  unit_name="$1"
  existed="$2"
  encoded="$3"
  target="${governance_unit_dir}/${unit_name}"
  case "${existed}" in
    true)
      if [ -z "${encoded}" ]; then
        echo "LiteLLM 治理 unit 回滚内容缺失: ${unit_name}"
        exit 1
      fi
      unit_temp="$(mktemp "${governance_unit_dir}/.${unit_name}.XXXXXX")"
      trap 'rm -f "${unit_temp}"' RETURN
      printf '%s' "${encoded}" | base64 --decode > "${unit_temp}"
      chmod 0644 "${unit_temp}"
      mv -f "${unit_temp}" "${target}"
      trap - RETURN
      ;;
    false)
      rm -f "${target}"
      ;;
    *) echo "LiteLLM 治理 unit 回滚状态无效: ${unit_name}"; exit 1 ;;
  esac
}
restore_governance_unit \
  fusion-litellm-governance.service \
  "${ROLLBACK_GOVERNANCE_SERVICE_UNIT_EXISTED}" \
  "${ROLLBACK_GOVERNANCE_SERVICE_UNIT_B64}"
restore_governance_unit \
  fusion-litellm-governance.timer \
  "${ROLLBACK_GOVERNANCE_TIMER_UNIT_EXISTED}" \
  "${ROLLBACK_GOVERNANCE_TIMER_UNIT_B64}"
restore_governance_unit \
  fusion-litellm-cost-sync.service \
  "${ROLLBACK_COST_SERVICE_UNIT_EXISTED}" \
  "${ROLLBACK_COST_SERVICE_UNIT_B64}"
restore_governance_unit \
  fusion-litellm-cost-sync.timer \
  "${ROLLBACK_COST_TIMER_UNIT_EXISTED}" \
  "${ROLLBACK_COST_TIMER_UNIT_B64}"
restore_governance_unit \
  fusion-litellm-model-management.service \
  "${ROLLBACK_MODEL_MANAGEMENT_SERVICE_UNIT_EXISTED}" \
  "${ROLLBACK_MODEL_MANAGEMENT_SERVICE_UNIT_B64}"
restore_governance_unit \
  fusion-litellm-model-management.timer \
  "${ROLLBACK_MODEL_MANAGEMENT_TIMER_UNIT_EXISTED}" \
  "${ROLLBACK_MODEL_MANAGEMENT_TIMER_UNIT_B64}"
systemctl --user daemon-reload
if [ "${ROLLBACK_GOVERNANCE_TIMER_ENABLED}" = "true" ]; then
  systemctl --user enable fusion-litellm-governance.timer
else
  systemctl --user disable fusion-litellm-governance.timer >/dev/null 2>&1 || true
fi
if [ "${ROLLBACK_GOVERNANCE_TIMER_ACTIVE}" = "true" ]; then
  systemctl --user start fusion-litellm-governance.timer
  systemctl --user is-active --quiet fusion-litellm-governance.timer
else
  systemctl --user stop fusion-litellm-governance.timer >/dev/null 2>&1 || true
fi
if [ "${ROLLBACK_COST_TIMER_ENABLED}" = "true" ]; then
  systemctl --user enable fusion-litellm-cost-sync.timer
else
  systemctl --user disable fusion-litellm-cost-sync.timer >/dev/null 2>&1 || true
fi
if [ "${ROLLBACK_COST_TIMER_ACTIVE}" = "true" ]; then
  systemctl --user start fusion-litellm-cost-sync.timer
  systemctl --user is-active --quiet fusion-litellm-cost-sync.timer
else
  systemctl --user stop fusion-litellm-cost-sync.timer >/dev/null 2>&1 || true
fi

echo "Restore model management worker after automatic rollback"
model_management_current_link="${HOME}/.local/share/fusion/litellm-model-management-current"
systemctl --user stop fusion-litellm-model-management.timer >/dev/null 2>&1 || true
if systemctl --user is-active --quiet fusion-litellm-model-management.service; then
  echo "模型准入 Worker 仍在执行，无法恢复回滚前版本"
  exit 1
fi
case "${ROLLBACK_MODEL_MANAGEMENT_TIMER_ENABLED}:${ROLLBACK_MODEL_MANAGEMENT_TIMER_ACTIVE}" in
  true:true|true:false|false:true|false:false) ;;
  *) echo "模型准入 Worker 回滚状态无效"; exit 1 ;;
esac
if [ -n "${ROLLBACK_MODEL_MANAGEMENT_CURRENT_TARGET}" ]; then
  case "${ROLLBACK_MODEL_MANAGEMENT_CURRENT_TARGET}" in
    "${HOME}/.local/share/fusion/litellm-model-management-src-"*) ;;
    *) echo "模型准入 Worker 回滚版本不在受管目录中"; exit 1 ;;
  esac
  if [ ! -d "${ROLLBACK_MODEL_MANAGEMENT_CURRENT_TARGET}" ]; then
    echo "模型准入 Worker 回滚版本不存在: ${ROLLBACK_MODEL_MANAGEMENT_CURRENT_TARGET}"
    exit 1
  fi
  if [ -e "${model_management_current_link}" ] && [ ! -L "${model_management_current_link}" ]; then
    echo "模型准入 Worker 当前版本路径不是符号链接，无法恢复"
    exit 1
  fi
  ln -sfn "${ROLLBACK_MODEL_MANAGEMENT_CURRENT_TARGET}" "${model_management_current_link}"
elif [ -L "${model_management_current_link}" ]; then
  unlink "${model_management_current_link}"
elif [ -e "${model_management_current_link}" ]; then
  echo "模型准入 Worker 当前版本路径不是符号链接，无法清理"
  exit 1
fi
systemctl --user daemon-reload
if [ "${ROLLBACK_MODEL_MANAGEMENT_TIMER_ENABLED}" = "true" ]; then
  systemctl --user enable fusion-litellm-model-management.timer
else
  systemctl --user disable fusion-litellm-model-management.timer >/dev/null 2>&1 || true
fi
if [ "${ROLLBACK_MODEL_MANAGEMENT_TIMER_ACTIVE}" = "true" ]; then
  systemctl --user start fusion-litellm-model-management.timer
  systemctl --user is-active --quiet fusion-litellm-model-management.timer
else
  systemctl --user stop fusion-litellm-model-management.timer >/dev/null 2>&1 || true
fi
echo "旧镜像已恢复且回滚后验收通过；原始发布失败状态保持不变"
