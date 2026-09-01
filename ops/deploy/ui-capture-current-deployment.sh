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
previousImageRef="$(docker inspect --format '{{.Config.Image}}' fusion-ui 2>/dev/null || true)"
previousImageId="$(docker inspect --format '{{.Image}}' fusion-ui 2>/dev/null || true)"
if [ -z "$previousImageRef" ] || [ -z "$previousImageId" ]; then
  echo "无法同时捕获 fusion-ui 当前镜像引用与镜像 ID，拒绝变更现有部署"
  docker ps --filter "name=fusion-ui"
  exit 1
fi

previousTagPrefix="${IMAGE_NAME}:"
previousDigestPrefix="${IMAGE_NAME}@sha256:"
case "$previousImageRef" in
  "$previousTagPrefix"*) previousImageSha="${previousImageRef#"$previousTagPrefix"}" ;;
  "$previousDigestPrefix"*)
    previousImageSha="$(python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" current-sha --path "${FUSION_UI_LEDGER}" --app ui)"
    expectedPreviousRef="$(python3 "${GITHUB_WORKSPACE}/.github/scripts/release_ledger.py" lookup --path "${FUSION_UI_LEDGER}" --app ui --sha "$previousImageSha" --component ui --field ref)"
    if [ "$previousImageRef" != "$expectedPreviousRef" ]; then
      echo "fusion-ui 当前摘要引用与发布账本不一致：expected=$expectedPreviousRef actual=$previousImageRef"
      exit 1
    fi
    ;;
  *) echo "fusion-ui 当前镜像引用不属于受管镜像仓库，拒绝作为回滚目标：$previousImageRef"; exit 1 ;;
esac
if ! [[ "$previousImageSha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "fusion-ui 当前镜像引用不是 40 位小写 Git SHA 标签，拒绝作为回滚目标：$previousImageRef"
  exit 1
fi
if ! [[ "$previousImageId" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "fusion-ui 当前镜像 ID 不是规范 sha256 内容 ID，拒绝作为回滚目标：$previousImageId"
  exit 1
fi

{
  printf '%s\n' "previous_image_ref=$previousImageRef"
  printf '%s\n' "previous_image_id=$previousImageId"
  printf '%s\n' "previous_sha=$previousImageSha"
} >> "$GITHUB_OUTPUT"
echo "已捕获当前 fusion-ui 部署：ref=$previousImageRef id=$previousImageId"
