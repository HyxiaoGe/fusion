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
required() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    printf '缺少参数化应用契约字段: %s\n' "${name}" >&2
    exit 1
  fi
}

for field in \
  DEPLOY_APP \
  DEPLOY_IMAGE_REPOSITORY \
  DEPLOY_HEALTH_CHECK_ENDPOINT \
  DEPLOY_MIGRATION_ENABLED \
  DEPLOY_DEPENDENCY_SERVICES \
  DEPLOY_ROLLBACK_ANCHOR_POLICY; do
  required "${field}"
done

case "${DEPLOY_APP}" in
  api)
    expected_repository="seanfield/fusion-api"
    expected_health_endpoint="http://127.0.0.1:8002/health"
    expected_migration_enabled="true"
    expected_dependency_services="postgres,redis,litellm,flyai-adapter,knowledge-worker"
    expected_rollback_anchor_policy="api-and-adapter-image-identities"
    rollback_anchors="fusion-api,fusion-flyai-adapter"
    ;;
  ui)
    expected_repository="seanfield/fusion-ui"
    expected_health_endpoint="http://127.0.0.1:3000/"
    expected_migration_enabled="false"
    expected_dependency_services="api"
    expected_rollback_anchor_policy="ui-image-identity"
    rollback_anchors="fusion-ui"
    ;;
  *)
    printf 'app 必须是 api 或 ui: %s\n' "${DEPLOY_APP}" >&2
    exit 1
    ;;
esac

if [ "${DEPLOY_IMAGE_REPOSITORY}" != "${expected_repository}" ]; then
  printf '镜像仓库与应用契约不匹配: expected=%s actual=%s\n' "${expected_repository}" "${DEPLOY_IMAGE_REPOSITORY}" >&2
  exit 1
fi
if [ "${DEPLOY_HEALTH_CHECK_ENDPOINT}" != "${expected_health_endpoint}" ]; then
  printf '健康检查端点与应用契约不匹配: expected=%s actual=%s\n' "${expected_health_endpoint}" "${DEPLOY_HEALTH_CHECK_ENDPOINT}" >&2
  exit 1
fi
if [ "${DEPLOY_MIGRATION_ENABLED}" != "${expected_migration_enabled}" ]; then
  printf '迁移开关与应用契约不匹配: expected=%s actual=%s\n' "${expected_migration_enabled}" "${DEPLOY_MIGRATION_ENABLED}" >&2
  exit 1
fi
if [ "${DEPLOY_DEPENDENCY_SERVICES}" != "${expected_dependency_services}" ]; then
  printf '依赖服务与应用契约不匹配: expected=%s actual=%s\n' "${expected_dependency_services}" "${DEPLOY_DEPENDENCY_SERVICES}" >&2
  exit 1
fi
if [ "${DEPLOY_ROLLBACK_ANCHOR_POLICY}" != "${expected_rollback_anchor_policy}" ]; then
  printf '回滚锚点策略与应用契约不匹配: expected=%s actual=%s\n' "${expected_rollback_anchor_policy}" "${DEPLOY_ROLLBACK_ANCHOR_POLICY}" >&2
  exit 1
fi

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  {
    printf '%s\n' "dependency_hooks=${DEPLOY_DEPENDENCY_SERVICES}"
    printf '%s\n' "rollback_anchors=${rollback_anchors}"
  } >> "${GITHUB_OUTPUT}"
fi
