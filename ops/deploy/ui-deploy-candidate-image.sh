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
cd "${FUSION_UI_RUNTIME_DIR}"
docker pull "${DEPLOY_UI_IMAGE}"
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
FUSION_UI_IMAGE="${DEPLOY_UI_IMAGE}" \
  docker compose --project-name fusion -f docker-compose.fusion-ui-ghcr.yml up -d
