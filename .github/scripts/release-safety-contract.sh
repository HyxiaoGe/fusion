#!/usr/bin/env bash
set -euo pipefail

manifest_path="${1:?缺少 release-safety 契约文件路径}"

case "${manifest_path}" in
  backend/release-safety.yml)
    test -f "${manifest_path}"
    python3 .github/scripts/test_workflow_contracts.py
    exec python3 backend/test/test_ci_cd_permission_boundary.py
    ;;
  frontend/release-safety.yml)
    test -f "${manifest_path}"
    cd frontend
    exec npx vitest run src/scripts/buildAndDeployWorkflow.test.ts
    ;;
  *)
    printf '不支持的 release-safety 契约: %s\n' "${manifest_path}" >&2
    exit 2
    ;;
esac
