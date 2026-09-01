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
chromePath="$(command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser || true)"
if [ -z "$chromePath" ]; then
  echo "browser smoke requires google-chrome/chromium on the dev runner"
  exit 1
fi
command -v node
command -v npm
echo "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=$chromePath" >> "$GITHUB_ENV"
echo "Resolved browser smoke Chrome: $chromePath"
