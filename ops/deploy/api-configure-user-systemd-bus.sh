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
runtime_dir="/run/user/$(id -u)"
test -d "${runtime_dir}"
test -S "${runtime_dir}/bus"
{
  printf '%s\n' "XDG_RUNTIME_DIR=${runtime_dir}"
  printf '%s\n' "DBUS_SESSION_BUS_ADDRESS=unix:path=${runtime_dir}/bus"
} >> "${GITHUB_ENV}"
XDG_RUNTIME_DIR="${runtime_dir}" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=${runtime_dir}/bus" \
  systemctl --user show-environment >/dev/null
