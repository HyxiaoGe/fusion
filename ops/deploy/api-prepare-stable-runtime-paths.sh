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
config_dir="${HOME}/.config/fusion"
runtime_env="${config_dir}/runtime.env"
legacy_env="${HOME}/project/fusion/.env"
runtime_dir="${HOME}/.local/share/fusion/api/runtime"
storage_dir="${HOME}/.local/share/fusion/api/storage/files"
ledger_path="${HOME}/.local/share/fusion/api/release-ledger.json"
install -d -m 0700 "${config_dir}" "$(dirname "${ledger_path}")"
install -d -m 0755 "${runtime_dir}" "${storage_dir}"
if [ ! -e "${runtime_env}" ]; then
  if [ ! -f "${legacy_env}" ] || [ -L "${legacy_env}" ]; then
    echo "缺少可迁移的旧运行时配置"
    exit 1
  fi
  install -m 0600 "${legacy_env}" "${runtime_env}"
fi
if [ ! -f "${runtime_env}" ] || [ -L "${runtime_env}" ]; then
  echo "运行时配置必须是普通文件"
  exit 1
fi
if [ "$(stat -c '%U:%a' "${runtime_env}")" != "$(id -un):600" ]; then
  echo "运行时配置 owner 或权限无效"
  exit 1
fi
set -a
source "${runtime_env}"
set +a
if [ "${STORAGE_BACKEND:-}" != "oss" ]; then
  echo "Task 2 仅允许已确认的 STORAGE_BACKEND=oss"
  exit 1
fi
{
  printf '%s\n' "FUSION_RUNTIME_ENV=${runtime_env}"
  printf '%s\n' "FUSION_API_RUNTIME_DIR=${runtime_dir}"
  printf '%s\n' "FUSION_STORAGE_DIR=${storage_dir}"
  printf '%s\n' "FUSION_API_LEDGER=${ledger_path}"
} >> "${GITHUB_ENV}"
