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
previousImageRef="${PREVIOUS_IMAGE_REF}"
previousImageId="${PREVIOUS_IMAGE_ID}"
if [ -z "$previousImageRef" ] || [ -z "$previousImageId" ]; then
  echo "自动回滚缺少旧镜像引用或镜像 ID"
  exit 1
fi

cd "${FUSION_UI_RUNTIME_DIR}"
cat > docker-compose.fusion-ui-ghcr.yml <<'EOF'
services:
  fusion-ui:
    image: ${FUSION_UI_IMAGE}
    container_name: fusion-ui
    restart: always
    ports:
      - "3004:3000"
    environment:
      - NODE_ENV=production
    deploy:
      resources:
        limits:
          memory: 256m
    networks:
      - postgres_default

networks:
  postgres_default:
    external: true
EOF
FUSION_UI_IMAGE="$previousImageRef" \
  docker compose --project-name fusion -f docker-compose.fusion-ui-ghcr.yml up -d

runningImageRef="$(docker inspect --format '{{.Config.Image}}' fusion-ui 2>/dev/null || true)"
runningImageId="$(docker inspect --format '{{.Image}}' fusion-ui 2>/dev/null || true)"
if [ "$runningImageRef" != "$previousImageRef" ]; then
  echo "fusion-ui rollback image reference mismatch: expected=$previousImageRef actual=${runningImageRef:-missing}"
  docker ps --filter "name=fusion-ui"
  docker logs --tail 80 fusion-ui || true
  exit 1
fi
if [ "$runningImageId" != "$previousImageId" ]; then
  echo "fusion-ui rollback image ID mismatch: expected=$previousImageId actual=${runningImageId:-missing}"
  docker ps --filter "name=fusion-ui"
  docker logs --tail 80 fusion-ui || true
  exit 1
fi

# 浏览器运行时本身可能是候选失败原因，回滚阻塞验收使用独立的容器内 HTTP 路径。
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if docker exec fusion-ui node -e 'fetch("http://127.0.0.1:3000/", { signal: AbortSignal.timeout(5000) }).then((response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); }).catch((error) => { console.error(error); process.exit(1); });'; then
    echo "fusion-ui rollback smoke check passed"
    exit 0
  fi
  sleep 2
done
echo "fusion-ui rollback smoke check failed"
docker ps --filter "name=fusion-ui"
docker logs --tail 80 fusion-ui || true
exit 1
