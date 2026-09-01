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
if [ -n "${ROLLBACK_SHA}" ]; then
  deploy_ref="$(python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" lookup --path "${FUSION_UI_LEDGER}" --app ui --sha "${DEPLOY_TARGET_SHA}" --component ui --field ref)"
else
  manifest_json="$(docker buildx imagetools inspect "${IMAGE_NAME}:${DEPLOY_TARGET_SHA}" --format '{{json .Manifest}}')"
  deploy_ref="$(printf '%s' "${manifest_json}" | python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" manifest-ref --repository "${IMAGE_NAME}")"
fi
docker pull "${deploy_ref}"
printf '%s\n' "DEPLOY_UI_IMAGE=${deploy_ref}" >> "${GITHUB_ENV}"
