#!/usr/bin/env bash
# 决定 PR 的前端测试范围：只改了 frontend/src 下已有源码时，只跑受影响的测试（vitest related）；
# 改到配置、依赖、测试初始化、删除/重命名文件，或不是 PR 时，跑全量。
# 输出：stdout 一行，空串表示全量，否则是相对 frontend/ 的文件列表（空格分隔）。
# 用法：ui_test_scope.sh <event_name>，需在 PR merge ref 上运行（HEAD^1 是目标分支）。
set -euo pipefail

event_name="${1:-}"
max_files=80

full() {
  echo "UI 测试范围：全量（$1）" >&2
  echo ""
  exit 0
}

[ "$event_name" = "pull_request" ] || full "非 PR 事件"
git rev-parse --verify --quiet HEAD^1 >/dev/null || full "拿不到目标分支提交"

related=()
while IFS=$'\t' read -r status path rest; do
  case "$path" in
    frontend/*) ;;
    *) continue ;;
  esac
  case "$status" in
    D*|R*|C*) full "删除或重命名了 $path" ;;
  esac
  [ -z "${rest:-}" ] || full "无法解析的改动 $path"
  case "$path" in
    *[[:space:]]*) full "路径含空白 $path" ;;
    frontend/src/test/*) full "改动测试初始化 $path" ;;
    frontend/src/*) related+=("${path#frontend/}") ;;
    *) full "改动 src 之外的前端文件 $path" ;;
  esac
done < <(git diff --name-status HEAD^1 HEAD)

[ "${#related[@]}" -gt 0 ] || full "没有前端源码改动"
[ "${#related[@]}" -le "$max_files" ] || full "改动文件超过 ${max_files} 个"

echo "UI 测试范围：只跑与 ${#related[@]} 个改动文件相关的测试" >&2
printf '%s\n' "${related[@]}" >&2
echo "${related[*]}"
