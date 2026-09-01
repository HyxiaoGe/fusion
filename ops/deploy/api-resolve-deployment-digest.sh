#!/usr/bin/env bash
set -e

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
if [ "${ROLLBACK_REQUESTED}" = "true" ]; then
  api_ref="$(python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" lookup --path "${FUSION_API_LEDGER}" --app api --sha "${DEPLOY_TARGET_SHA}" --component api --field ref)"
  adapter_ref="$(python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" lookup --path "${FUSION_API_LEDGER}" --app api --sha "${DEPLOY_TARGET_SHA}" --component adapter --field ref)"
else
  api_ref="$(resolve_digest_ref "${IMAGE_NAME}" "${IMAGE_NAME}:${DEPLOY_TARGET_SHA}")"
  adapter_ref="$(resolve_digest_ref "${FLYAI_ADAPTER_IMAGE_NAME}" "${FLYAI_ADAPTER_IMAGE_NAME}:${DEPLOY_TARGET_SHA}")"
fi
docker pull "${api_ref}"
docker pull "${adapter_ref}"
{
  printf '%s\n' "DEPLOY_API_IMAGE=${api_ref}"
  printf '%s\n' "DEPLOY_ADAPTER_IMAGE=${adapter_ref}"
} >> "${GITHUB_ENV}"
