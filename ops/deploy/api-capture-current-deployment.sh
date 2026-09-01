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
set -o pipefail
if [[ ! "${DEPLOY_TARGET_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "部署目标不是 40 位小写 Git SHA，拒绝变更部署"
  exit 1
fi
if [ "${DEPLOY_REQUIRED_DEPENDENCY_HOOKS:-}" != "postgres,redis,litellm,flyai-adapter,knowledge-worker" ]; then
  echo "API 依赖服务契约无效，拒绝选择不完整的部署 hooks"
  exit 1
fi
case "${DEPLOY_REQUIRED_ROLLBACK_ANCHORS:-}" in
  fusion-api,fusion-flyai-adapter) rollback_anchors=(fusion-api fusion-flyai-adapter) ;;
  *) echo "API 回滚锚点策略未提供 api 与 adapter 的运行镜像身份"; exit 1 ;;
esac
for rollback_anchor in "${rollback_anchors[@]}"; do
  if ! docker container inspect "${rollback_anchor}" >/dev/null 2>&1; then
    echo "${rollback_anchor} 不存在，拒绝在缺少回滚目标时变更部署"
    exit 1
  fi
done
if ! docker container inspect fusion-api >/dev/null 2>&1; then
  echo "fusion-api 不存在，拒绝在缺少回滚目标时变更部署"
  exit 1
fi
if ! docker container inspect fusion-flyai-adapter >/dev/null 2>&1; then
  echo "fusion-flyai-adapter 不存在，拒绝在缺少回滚目标时变更部署"
  exit 1
fi

api_image_ref="$(docker inspect fusion-api --format '{{.Config.Image}}')"
api_image_id="$(docker inspect fusion-api --format '{{.Image}}')"
adapter_image_ref="$(docker inspect fusion-flyai-adapter --format '{{.Config.Image}}')"
adapter_image_id="$(docker inspect fusion-flyai-adapter --format '{{.Image}}')"
api_tag_prefix="${IMAGE_NAME}:"
api_digest_prefix="${IMAGE_NAME}@sha256:"
case "${api_image_ref}" in
  "${api_tag_prefix}"*)
    api_sha="${api_image_ref#"${api_tag_prefix}"}"
    expected_adapter_ref="${FLYAI_ADAPTER_IMAGE_NAME}:${api_sha}"
    ;;
  "${api_digest_prefix}"*)
    api_sha="$(python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" current-sha --path "${FUSION_API_LEDGER}" --app api)"
    expected_api_ref="$(python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" lookup --path "${FUSION_API_LEDGER}" --app api --sha "${api_sha}" --component api --field ref)"
    expected_adapter_ref="$(python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" lookup --path "${FUSION_API_LEDGER}" --app api --sha "${api_sha}" --component adapter --field ref)"
    if [ "${api_image_ref}" != "${expected_api_ref}" ]; then
      echo "fusion-api 当前摘要引用与发布账本不一致: expected=${expected_api_ref} actual=${api_image_ref}"
      exit 1
    fi
    ;;
  *) echo "fusion-api 镜像仓库不受当前 workflow 管理: ${api_image_ref}"; exit 1 ;;
esac
if [[ ! "${api_sha}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "fusion-api 发布 SHA 不是 40 位小写 Git SHA: ${api_sha}"
  exit 1
fi
if [ "${adapter_image_ref}" != "${expected_adapter_ref}" ]; then
  echo "fusion-flyai-adapter 与 fusion-api 未运行同一 SHA: expected=${expected_adapter_ref} actual=${adapter_image_ref}"
  exit 1
fi
if [[ ! "${api_image_id}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "fusion-api 内容 ID 格式无效: ${api_image_id}"
  exit 1
fi
if [[ ! "${adapter_image_id}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "fusion-flyai-adapter 内容 ID 格式无效: ${adapter_image_id}"
  exit 1
fi
knowledge_worker_existed="false"
knowledge_worker_image_ref=""
knowledge_worker_image_id=""
if docker container inspect fusion-knowledge-worker >/dev/null 2>&1; then
  knowledge_worker_existed="true"
  knowledge_worker_image_ref="$(docker inspect fusion-knowledge-worker --format '{{.Config.Image}}')"
  knowledge_worker_image_id="$(docker inspect fusion-knowledge-worker --format '{{.Image}}')"
  if [ "${knowledge_worker_image_ref}" != "${api_image_ref}" ]; then
    echo "fusion-knowledge-worker 与 fusion-api 未运行同一镜像: api=${api_image_ref} worker=${knowledge_worker_image_ref}"
    exit 1
  fi
  if [ "${knowledge_worker_image_id}" != "${api_image_id}" ]; then
    echo "fusion-knowledge-worker 与 fusion-api 内容 ID 不一致"
    exit 1
  fi
fi

rollback_knowledge_config_file="${RUNNER_TEMP}/fusion-knowledge-rollback-${GITHUB_RUN_ID}-${GITHUB_JOB}-${GITHUB_RUN_ATTEMPT}.env"
case "${rollback_knowledge_config_file}" in
  "${RUNNER_TEMP}"/fusion-knowledge-rollback-*.env) ;;
  *) echo "知识库回滚配置未落在 RUNNER_TEMP 的受管路径中"; exit 1 ;;
esac
export ROLLBACK_KNOWLEDGE_CONFIG_FILE="${rollback_knowledge_config_file}"
rollback_containers=("${rollback_anchors[0]}")
if [ "${knowledge_worker_existed}" = "true" ]; then
  rollback_containers+=(fusion-knowledge-worker)
fi
umask 077
docker inspect "${rollback_containers[@]}" | python3 -c '
import json
import os
import shlex
import sys

defaults = {
    "KNOWLEDGE_BASE_ENABLED": "false",
    "KNOWLEDGE_MAX_BASES_PER_USER": "50",
    "KNOWLEDGE_MAX_DOCUMENTS_PER_BASE": "100",
    "KNOWLEDGE_MAX_FILE_SIZE": str(10 * 1024 * 1024),
    "KNOWLEDGE_ALLOWED_MIME_TYPES": "text/plain,text/markdown,text/csv,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "KNOWLEDGE_PARSER_VERSION": "parser-v1",
    "KNOWLEDGE_CHUNKER_VERSION": "chunker-v1",
    "KNOWLEDGE_CHUNK_SIZE": "1200",
    "KNOWLEDGE_CHUNK_OVERLAP": "200",
    "KNOWLEDGE_MAX_CHUNKS_PER_DOCUMENT": "10000",
    "KNOWLEDGE_PARSE_TIMEOUT_SECONDS": "60",
    "KNOWLEDGE_EMBEDDING_PROVIDER": "litellm",
    "KNOWLEDGE_EMBEDDING_MODEL": "",
    "KNOWLEDGE_EMBEDDING_REVISION": "",
    "KNOWLEDGE_EMBEDDING_REVISION_ROUTES": "{}",
    "KNOWLEDGE_EMBEDDING_DIMENSION": "1024",
    "KNOWLEDGE_EMBEDDING_ALLOWED_DIMENSIONS": "1024",
    "KNOWLEDGE_EMBEDDING_BATCH_SIZE": "32",
    "KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS": "30",
    "KNOWLEDGE_SEARCH_MAX_PROFILES": "8",
    "KNOWLEDGE_DISTANCE_METRIC": "COSINE",
    "KNOWLEDGE_WORKER_POLL_SECONDS": "2",
    "KNOWLEDGE_WORKER_LEASE_SECONDS": "180",
    "KNOWLEDGE_WORKER_HEARTBEAT_SECONDS": "30",
    "KNOWLEDGE_WORKER_MAX_ATTEMPTS": "5",
    "KNOWLEDGE_WORKER_RETRY_BASE_SECONDS": "5",
    "KNOWLEDGE_WORKER_RETRY_MAX_SECONDS": "300",
    "KNOWLEDGE_WORKER_HEALTH_FILE": "/tmp/fusion-knowledge-worker-health.json",
    "MILVUS_URI": "",
    "MILVUS_USERNAME": "",
    "MILVUS_PASSWORD": "",
    "MILVUS_DATABASE": "",
    "MILVUS_COLLECTION_PREFIX": "fusion_knowledge_chunks",
    "MILVUS_TIMEOUT_SECONDS": "10",
    "MILVUS_DOCKER_NETWORK": "fusion_knowledge_milvus",
}
known_networks = {"postgres_default", "middleware_default", "fusion-prompthub", "fusion-flyai"}

def read_config(container):
    captured = {}
    for entry in container.get("Config", {}).get("Env") or []:
        name, separator, value = entry.partition("=")
        if not separator or name not in defaults:
            continue
        if name in captured:
            raise SystemExit("duplicate knowledge container environment")
        captured[name] = value
    if "MILVUS_DOCKER_NETWORK" not in captured:
        networks = set((container.get("NetworkSettings", {}).get("Networks") or {}).keys())
        candidates = networks - known_networks
        if len(candidates) > 1:
            raise SystemExit("ambiguous knowledge Milvus network")
        if candidates:
            captured["MILVUS_DOCKER_NETWORK"] = candidates.pop()
    return {name: captured.get(name, default) for name, default in defaults.items()}

containers = json.load(sys.stdin)
if not containers:
    raise SystemExit("missing rollback container configuration")
configs = [read_config(container) for container in containers]
if any(config != configs[0] for config in configs[1:]):
    raise SystemExit("knowledge API and Worker configuration mismatch")
config = configs[0]
enabled = config["KNOWLEDGE_BASE_ENABLED"].strip().lower()
if enabled not in {"true", "false"}:
    raise SystemExit("invalid knowledge feature flag")
config["KNOWLEDGE_BASE_ENABLED"] = enabled
if enabled == "true" and any(
    not config[name]
    for name in (
        "KNOWLEDGE_EMBEDDING_MODEL",
        "KNOWLEDGE_EMBEDDING_REVISION",
        "MILVUS_URI",
        "MILVUS_USERNAME",
        "MILVUS_PASSWORD",
        "MILVUS_DATABASE",
    )
):
    raise SystemExit("incomplete enabled knowledge configuration")
path = os.environ["ROLLBACK_KNOWLEDGE_CONFIG_FILE"]
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
with os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8") as stream:
    for name, value in config.items():
        stream.write(f"export {name}={shlex.quote(value)}\n")
'

capture_container_bool_env() {
  key="$1"
  docker inspect fusion-api --format '{{json .Config.Env}}' | python3 -c '
import json
import sys

key = sys.argv[1]
entries = json.load(sys.stdin)
values = [entry.split("=", 1)[1] for entry in entries if entry.startswith(f"{key}=")]
if len(values) > 1:
    raise SystemExit("duplicate container boolean environment")
value = values[0].strip().lower() if values else "false"
if value not in {"true", "false"}:
    raise SystemExit("invalid container boolean environment")
print(value)
' "${key}"
}
rollback_model_management_enabled="$(capture_container_bool_env LITELLM_MODEL_MANAGEMENT_ENABLED)" || {
  echo "无法读取部署前的模型管理开关"
  exit 1
}
rollback_model_admission_worker_enabled="$(capture_container_bool_env LITELLM_MODEL_ADMISSION_WORKER_ENABLED)" || {
  echo "无法读取部署前的模型准入 Worker 开关"
  exit 1
}
case "${rollback_model_management_enabled}:${rollback_model_admission_worker_enabled}" in
  true:true|true:false|false:false) ;;
  *) echo "部署前的模型管理开关组合无效，拒绝在无法安全回滚时部署"; exit 1 ;;
esac

model_management_current_link="${HOME}/.local/share/fusion/litellm-model-management-current"
model_management_current_target=""
if [ -L "${model_management_current_link}" ]; then
  model_management_current_target="$(readlink -f -- "${model_management_current_link}")"
  case "${model_management_current_target}" in
    "${HOME}/.local/share/fusion/litellm-model-management-src-"*) ;;
    *) echo "模型准入 Worker 当前版本不在受管目录中: ${model_management_current_target:-<empty>}"; exit 1 ;;
  esac
elif [ -e "${model_management_current_link}" ]; then
  echo "模型准入 Worker 当前版本路径不是符号链接，拒绝部署"
  exit 1
fi
model_management_timer_enabled="false"
model_management_timer_active="false"
if systemctl --user is-enabled --quiet fusion-litellm-model-management.timer; then
  model_management_timer_enabled="true"
fi
if systemctl --user is-active --quiet fusion-litellm-model-management.timer; then
  model_management_timer_active="true"
fi

governance_current_link="${HOME}/.local/share/fusion/litellm-governance-current"
governance_current_target=""
if [ -L "${governance_current_link}" ]; then
  governance_current_target="$(readlink -f -- "${governance_current_link}")"
  case "${governance_current_target}" in
    "${HOME}/.local/share/fusion/litellm-governance-src-"*) ;;
    *) echo "LiteLLM 治理当前版本不在受管目录中: ${governance_current_target:-<empty>}"; exit 1 ;;
  esac
elif [ -e "${governance_current_link}" ]; then
  echo "LiteLLM 治理当前版本路径不是符号链接，拒绝部署"
  exit 1
fi
governance_timer_enabled="false"
governance_timer_active="false"
cost_timer_enabled="false"
cost_timer_active="false"
if systemctl --user is-enabled --quiet fusion-litellm-governance.timer; then
  governance_timer_enabled="true"
fi
if systemctl --user is-active --quiet fusion-litellm-governance.timer; then
  governance_timer_active="true"
fi
if systemctl --user is-enabled --quiet fusion-litellm-cost-sync.timer; then
  cost_timer_enabled="true"
fi
if systemctl --user is-active --quiet fusion-litellm-cost-sync.timer; then
  cost_timer_active="true"
fi
governance_unit_dir="${HOME}/.config/systemd/user"
governance_service_unit="${governance_unit_dir}/fusion-litellm-governance.service"
governance_timer_unit="${governance_unit_dir}/fusion-litellm-governance.timer"
governance_service_unit_existed="false"
governance_timer_unit_existed="false"
governance_service_unit_b64=""
governance_timer_unit_b64=""
if [ -e "${governance_service_unit}" ]; then
  if [ ! -f "${governance_service_unit}" ] || [ -L "${governance_service_unit}" ]; then
    echo "LiteLLM 治理 service unit 不是受管普通文件"
    exit 1
  fi
  governance_service_unit_existed="true"
  governance_service_unit_b64="$(base64 -w0 "${governance_service_unit}")"
fi
if [ -e "${governance_timer_unit}" ]; then
  if [ ! -f "${governance_timer_unit}" ] || [ -L "${governance_timer_unit}" ]; then
    echo "LiteLLM 治理 timer unit 不是受管普通文件"
    exit 1
  fi
  governance_timer_unit_existed="true"
  governance_timer_unit_b64="$(base64 -w0 "${governance_timer_unit}")"
fi
cost_service_unit="${governance_unit_dir}/fusion-litellm-cost-sync.service"
cost_timer_unit="${governance_unit_dir}/fusion-litellm-cost-sync.timer"
cost_service_unit_existed="false"
cost_timer_unit_existed="false"
cost_service_unit_b64=""
cost_timer_unit_b64=""
if [ -e "${cost_service_unit}" ]; then
  if [ ! -f "${cost_service_unit}" ] || [ -L "${cost_service_unit}" ]; then
    echo "LiteLLM 成本同步 service unit 不是受管普通文件"
    exit 1
  fi
  cost_service_unit_existed="true"
  cost_service_unit_b64="$(base64 -w0 "${cost_service_unit}")"
fi
if [ -e "${cost_timer_unit}" ]; then
  if [ ! -f "${cost_timer_unit}" ] || [ -L "${cost_timer_unit}" ]; then
    echo "LiteLLM 成本同步 timer unit 不是受管普通文件"
    exit 1
  fi
  cost_timer_unit_existed="true"
  cost_timer_unit_b64="$(base64 -w0 "${cost_timer_unit}")"
fi
model_management_service_unit="${governance_unit_dir}/fusion-litellm-model-management.service"
model_management_timer_unit="${governance_unit_dir}/fusion-litellm-model-management.timer"
model_management_service_unit_existed="false"
model_management_timer_unit_existed="false"
model_management_service_unit_b64=""
model_management_timer_unit_b64=""
if [ -e "${model_management_service_unit}" ]; then
  if [ ! -f "${model_management_service_unit}" ] || [ -L "${model_management_service_unit}" ]; then
    echo "模型准入 Worker service unit 不是受管普通文件"
    exit 1
  fi
  model_management_service_unit_existed="true"
  model_management_service_unit_b64="$(base64 -w0 "${model_management_service_unit}")"
fi
if [ -e "${model_management_timer_unit}" ]; then
  if [ ! -f "${model_management_timer_unit}" ] || [ -L "${model_management_timer_unit}" ]; then
    echo "模型准入 Worker timer unit 不是受管普通文件"
    exit 1
  fi
  model_management_timer_unit_existed="true"
  model_management_timer_unit_b64="$(base64 -w0 "${model_management_timer_unit}")"
fi

{
  printf '%s\n' "api_image_ref=${api_image_ref}"
  printf '%s\n' "api_image_id=${api_image_id}"
  printf '%s\n' "adapter_image_ref=${adapter_image_ref}"
  printf '%s\n' "adapter_image_id=${adapter_image_id}"
  printf '%s\n' "deployment_sha=${api_sha}"
  printf '%s\n' "knowledge_worker_existed=${knowledge_worker_existed}"
  printf '%s\n' "knowledge_worker_image_ref=${knowledge_worker_image_ref}"
  printf '%s\n' "knowledge_worker_image_id=${knowledge_worker_image_id}"
  printf '%s\n' "model_management_enabled=${rollback_model_management_enabled}"
  printf '%s\n' "model_admission_worker_enabled=${rollback_model_admission_worker_enabled}"
  printf '%s\n' "model_management_current_target=${model_management_current_target}"
  printf '%s\n' "model_management_timer_enabled=${model_management_timer_enabled}"
  printf '%s\n' "model_management_timer_active=${model_management_timer_active}"
  printf '%s\n' "governance_current_target=${governance_current_target}"
  printf '%s\n' "governance_timer_enabled=${governance_timer_enabled}"
  printf '%s\n' "governance_timer_active=${governance_timer_active}"
  printf '%s\n' "cost_timer_enabled=${cost_timer_enabled}"
  printf '%s\n' "cost_timer_active=${cost_timer_active}"
  printf '%s\n' "governance_service_unit_existed=${governance_service_unit_existed}"
  printf '%s\n' "governance_timer_unit_existed=${governance_timer_unit_existed}"
  printf '%s\n' "governance_service_unit_b64=${governance_service_unit_b64}"
  printf '%s\n' "governance_timer_unit_b64=${governance_timer_unit_b64}"
  printf '%s\n' "cost_service_unit_existed=${cost_service_unit_existed}"
  printf '%s\n' "cost_timer_unit_existed=${cost_timer_unit_existed}"
  printf '%s\n' "cost_service_unit_b64=${cost_service_unit_b64}"
  printf '%s\n' "cost_timer_unit_b64=${cost_timer_unit_b64}"
  printf '%s\n' "model_management_service_unit_existed=${model_management_service_unit_existed}"
  printf '%s\n' "model_management_timer_unit_existed=${model_management_timer_unit_existed}"
  printf '%s\n' "model_management_service_unit_b64=${model_management_service_unit_b64}"
  printf '%s\n' "model_management_timer_unit_b64=${model_management_timer_unit_b64}"
} >> "${GITHUB_OUTPUT}"
