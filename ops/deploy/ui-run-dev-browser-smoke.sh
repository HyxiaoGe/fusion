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
smokeNodeDir="$HOME/.cache/fusion-ui-smoke/playwright-1.58.2"
if [ ! -f "$smokeNodeDir/node_modules/playwright/index.js" ]; then
  rm -rf "$smokeNodeDir"
  mkdir -p "$smokeNodeDir"
  PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --prefix "$smokeNodeDir" --no-save --package-lock=false --ignore-scripts --no-audit --no-fund playwright@1.58.2
fi

if ! PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
  PLAYWRIGHT_MODULE_PATH="$smokeNodeDir/node_modules/playwright/index.js" \
  PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH="$PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH" \
  node frontend/scripts/smoke-dev-deployment.mjs; then
  echo "fusion-ui dev browser smoke failed"
  docker ps --filter "name=fusion-ui"
  docker logs --tail 80 fusion-ui || true
  exit 1
fi
