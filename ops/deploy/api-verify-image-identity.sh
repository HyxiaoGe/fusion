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
expected_api_image="${DEPLOY_API_IMAGE}"
actual_api_image="$(docker inspect fusion-api --format '{{.Config.Image}}')"
if [ "${actual_api_image}" != "${expected_api_image}" ]; then
  echo "fusion-api 镜像不匹配: expected=${expected_api_image} actual=${actual_api_image}"
  exit 1
fi
expected_api_id="$(docker image inspect "${expected_api_image}" --format '{{.Id}}')"
actual_api_id="$(docker inspect fusion-api --format '{{.Image}}')"
if [ "${actual_api_id}" != "${expected_api_id}" ]; then
  echo "fusion-api 内容 ID 不匹配: expected=${expected_api_id} actual=${actual_api_id}"
  exit 1
fi

expected_adapter_image="${DEPLOY_ADAPTER_IMAGE}"
actual_adapter_image="$(docker inspect fusion-flyai-adapter --format '{{.Config.Image}}')"
if [ "${actual_adapter_image}" != "${expected_adapter_image}" ]; then
  echo "fusion-flyai-adapter 镜像不匹配: expected=${expected_adapter_image} actual=${actual_adapter_image}"
  exit 1
fi
expected_adapter_id="$(docker image inspect "${expected_adapter_image}" --format '{{.Id}}')"
actual_adapter_id="$(docker inspect fusion-flyai-adapter --format '{{.Image}}')"
if [ "${actual_adapter_id}" != "${expected_adapter_id}" ]; then
  echo "fusion-flyai-adapter 内容 ID 不匹配: expected=${expected_adapter_id} actual=${actual_adapter_id}"
  exit 1
fi
if [ "${KNOWLEDGE_WORKER_SUPPORTED}" = "true" ]; then
  actual_worker_image="$(docker inspect fusion-knowledge-worker --format '{{.Config.Image}}')"
  actual_worker_id="$(docker inspect fusion-knowledge-worker --format '{{.Image}}')"
  if [ "${actual_worker_image}" != "${expected_api_image}" ] \
    || [ "${actual_worker_id}" != "${expected_api_id}" ]; then
    echo "fusion-knowledge-worker 未运行候选 API 镜像"
    exit 1
  fi
elif docker container inspect fusion-knowledge-worker >/dev/null 2>&1; then
  echo "回滚目标不支持知识库 Worker，但旧容器仍存在"
  exit 1
fi
