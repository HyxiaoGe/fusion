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
expectedImage="${DEPLOY_UI_IMAGE}"
runningImage="$(docker inspect --format '{{.Config.Image}}' fusion-ui 2>/dev/null || true)"
if [ "$runningImage" != "$expectedImage" ]; then
  echo "fusion-ui image identity mismatch: expected=$expectedImage actual=${runningImage:-missing}"
  docker ps --filter "name=fusion-ui"
  docker logs --tail 80 fusion-ui || true
  exit 1
fi

expectedImageId="$(docker image inspect --format '{{.Id}}' "$expectedImage" 2>/dev/null || true)"
runningImageId="$(docker inspect --format '{{.Image}}' fusion-ui 2>/dev/null || true)"
if [ -z "$expectedImageId" ]; then
  echo "fusion-ui candidate image ID is missing: image=$expectedImage"
  exit 1
fi
if [ "$runningImageId" != "$expectedImageId" ]; then
  echo "fusion-ui image ID mismatch: expected=${expectedImageId:-missing} actual=${runningImageId:-missing}"
  docker ps --filter "name=fusion-ui"
  docker logs --tail 80 fusion-ui || true
  exit 1
fi

for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if docker exec fusion-ui node -e 'fetch("http://127.0.0.1:3000/", { signal: AbortSignal.timeout(5000) }).then((response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); }).catch((error) => { console.error(error); process.exit(1); });'; then
    exit 0
  fi
  sleep 2
done
echo "fusion-ui dev smoke check failed"
docker ps --filter "name=fusion-ui"
docker logs --tail 80 fusion-ui || true
exit 1
