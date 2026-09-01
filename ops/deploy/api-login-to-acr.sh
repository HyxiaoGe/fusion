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
# 登录失败时重试退避（境内 ACR 通常稳定，保留重试作防御）
n=0
until printf '%s' "${ACR_PASSWORD}" | docker login "${REGISTRY}" -u "${ACR_USERNAME}" --password-stdin; do
  n=$((n+1))
  if [ $n -ge 6 ]; then echo "docker login 重试 $n 次仍失败"; exit 1; fi
  echo "docker login 失败（网络抖动），第 $n 次，10s 后重试..."
  sleep 10
done
