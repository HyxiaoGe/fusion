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
  local manifest_json
  manifest_json="$(docker buildx imagetools inspect "$1" --format '{{json .Manifest}}')"
  printf '%s' "${manifest_json}" | python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" manifest-ref --repository "${IMAGE_NAME}"
}
case "${PREVIOUS_REF}" in
  "${IMAGE_NAME}:"*)
    digest_ref="$(resolve_digest_ref "${PREVIOUS_REF}")"
    docker pull "${digest_ref}"
    image_id="$(docker image inspect --format '{{.Id}}' "${digest_ref}")"
    if [ "${image_id}" != "${PREVIOUS_ID}" ]; then
      echo "旧 UI 标签当前 manifest 与运行中镜像身份不一致，拒绝建立回滚基线"
      exit 1
    fi
    python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" record \
      --path "${FUSION_UI_LEDGER}" --app ui --sha "${PREVIOUS_SHA}" \
      --run-id "bootstrap-${GITHUB_RUN_ID}" --recorded-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --image "ui|${digest_ref}|${image_id}"
    ;;
  "${IMAGE_NAME}@sha256:"*)
    expected_ref="$(python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" lookup --path "${FUSION_UI_LEDGER}" --app ui --sha "${PREVIOUS_SHA}" --component ui --field ref)"
    expected_id="$(python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" lookup --path "${FUSION_UI_LEDGER}" --app ui --sha "${PREVIOUS_SHA}" --component ui --field image_id)"
    if [ "${PREVIOUS_REF}|${PREVIOUS_ID}" != "${expected_ref}|${expected_id}" ]; then
      echo "运行中 UI 镜像身份与发布账本不一致"
      exit 1
    fi
    ;;
  *) echo "旧 UI 镜像引用不受管理: ${PREVIOUS_REF}"; exit 1 ;;
esac
