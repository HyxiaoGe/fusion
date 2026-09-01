#!/usr/bin/env bash
set -euo pipefail

image_name="${1:?缺少 API 镜像名}"
adapter_image_name="${2:?缺少 FlyAI Adapter 镜像名}"
image_tag="${3:?缺少镜像标签}"
image="${image_name}:${image_tag}"
adapter_image="${adapter_image_name}:${image_tag}"
app_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

docker build --target production -t "${image}" "${app_root}"

docker run --rm \
  --mount "type=bind,source=${app_root}/README.md,target=/app/README.md,readonly" \
  "${image}" sh -lc "timeout 300s python -m pip install --default-timeout=30 --no-cache-dir -r requirements-ci.txt && python scripts/check_architecture.py && ruff check . && timeout 270s python -u -m unittest discover -s test -t . -v && timeout 120s python -m pytest -q test/services/stream/test_run_capability_router.py test/ai/skills/test_registry.py"

docker build --target test -t "${adapter_image}-test" "${app_root}/flyai-adapter"
docker build --target production -t "${adapter_image}" "${app_root}/flyai-adapter"
