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
set -euo pipefail
resolve_digest_ref() {
  local repository="$1"
  local tag_ref="$2"
  local manifest_json
  manifest_json="$(docker buildx imagetools inspect "${tag_ref}" --format '{{json .Manifest}}')"
  printf '%s' "${manifest_json}" | python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" manifest-ref --repository "${repository}"
}
case "${PREVIOUS_API_REF}" in
  "${IMAGE_NAME}:"*)
    api_ref="$(resolve_digest_ref "${IMAGE_NAME}" "${PREVIOUS_API_REF}")"
    adapter_ref="$(resolve_digest_ref "${FLYAI_ADAPTER_IMAGE_NAME}" "${PREVIOUS_ADAPTER_REF}")"
    docker pull "${api_ref}"
    docker pull "${adapter_ref}"
    api_id="$(docker image inspect --format '{{.Id}}' "${api_ref}")"
    adapter_id="$(docker image inspect --format '{{.Id}}' "${adapter_ref}")"
    if [ "${api_id}" != "${PREVIOUS_API_ID}" ] || [ "${adapter_id}" != "${PREVIOUS_ADAPTER_ID}" ]; then
      echo "旧 API 标签当前 manifest 与运行中镜像身份不一致，拒绝建立回滚基线"
      exit 1
    fi
    python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" record \
      --path "${FUSION_API_LEDGER}" --app api --sha "${PREVIOUS_SHA}" \
      --run-id "bootstrap-${GITHUB_RUN_ID}" --recorded-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --image "api|${api_ref}|${api_id}" \
      --image "adapter|${adapter_ref}|${adapter_id}"
    ;;
  "${IMAGE_NAME}@sha256:"*)
    expected_api_ref="$(python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" lookup --path "${FUSION_API_LEDGER}" --app api --sha "${PREVIOUS_SHA}" --component api --field ref)"
    expected_api_id="$(python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" lookup --path "${FUSION_API_LEDGER}" --app api --sha "${PREVIOUS_SHA}" --component api --field image_id)"
    expected_adapter_ref="$(python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" lookup --path "${FUSION_API_LEDGER}" --app api --sha "${PREVIOUS_SHA}" --component adapter --field ref)"
    expected_adapter_id="$(python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" lookup --path "${FUSION_API_LEDGER}" --app api --sha "${PREVIOUS_SHA}" --component adapter --field image_id)"
    if [ "${PREVIOUS_API_REF}|${PREVIOUS_API_ID}|${PREVIOUS_ADAPTER_REF}|${PREVIOUS_ADAPTER_ID}" != "${expected_api_ref}|${expected_api_id}|${expected_adapter_ref}|${expected_adapter_id}" ]; then
      echo "运行中 API 镜像身份与发布账本不一致"
      exit 1
    fi
    ;;
  *) echo "旧 API 镜像引用不受管理: ${PREVIOUS_API_REF}"; exit 1 ;;
esac
