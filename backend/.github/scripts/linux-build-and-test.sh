#!/usr/bin/env bash
set -euo pipefail

image_name="${1:?缺少 API 镜像名}"
adapter_image_name="${2:?缺少 FlyAI Adapter 镜像名}"
image_tag="${3:?缺少镜像标签}"
image="${image_name}:${image_tag}"
adapter_image="${adapter_image_name}:${image_tag}"
app_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
monorepo_root="$(cd "${app_root}/.." && pwd)"

if [ -n "${API_BUILD_CACHE_DIR:-}" ]; then
  # CI 用本地目录做层缓存（由 actions/cache 按 Dockerfile 与依赖清单还原），依赖不变时跳过 apt/pip 安装。
  builder="fusion-api-ci-$$"
  docker buildx create --name "${builder}" --driver docker-container >/dev/null
  trap 'docker buildx rm "${builder}" >/dev/null 2>&1 || true' EXIT
  cache_from=()
  if [ -d "${API_BUILD_CACHE_DIR}" ]; then
    cache_from=(--cache-from "type=local,src=${API_BUILD_CACHE_DIR}")
  fi
  docker buildx build \
    --builder "${builder}" \
    --target production \
    --load \
    --provenance=false \
    "${cache_from[@]}" \
    --cache-to "type=local,dest=${API_BUILD_CACHE_DIR}.new,mode=max" \
    -t "${image}" \
    "${app_root}"
  rm -rf "${API_BUILD_CACHE_DIR}"
  mv "${API_BUILD_CACHE_DIR}.new" "${API_BUILD_CACHE_DIR}"
else
  docker build --target production -t "${image}" "${app_root}"
fi

docker run --rm \
  --mount "type=bind,source=${app_root}/README.md,target=/app/README.md,readonly" \
  --mount "type=bind,source=${monorepo_root}/.github,target=/.github,readonly" \
  --mount "type=bind,source=${monorepo_root}/ops,target=/ops,readonly" \
  "${image}" sh -lc "timeout 300s python -m pip install --default-timeout=30 --no-cache-dir -r requirements-ci.txt && python scripts/check_architecture.py && ruff check . && timeout 420s python -u -m pytest -v -p no:cacheprovider test"

docker build --target test -t "${adapter_image}-test" "${app_root}/flyai-adapter"
docker build --target production -t "${adapter_image}" "${app_root}/flyai-adapter"
