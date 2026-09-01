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
runtime_dir="${HOME}/.local/share/fusion/ui/runtime"
ledger_path="${HOME}/.local/share/fusion/ui/release-ledger.json"
install -d -m 0755 "${runtime_dir}"
install -d -m 0700 "$(dirname "${ledger_path}")"
{
  printf '%s\n' "FUSION_UI_RUNTIME_DIR=${runtime_dir}"
  printf '%s\n' "FUSION_UI_LEDGER=${ledger_path}"
} >> "${GITHUB_ENV}"
