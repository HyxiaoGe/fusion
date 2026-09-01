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
if [ -z "$FEISHU_INFRA_WEBHOOK" ]; then
  echo "FEISHU_INFRA_WEBHOOK 未配置,跳过飞书通知"
  exit 0
fi
export SHORT_SHA="${DEPLOY_TARGET_SHA:0:7}"
export DEPLOY_TS="$(date -u '+%Y-%m-%d %H:%M UTC')"
python3 - <<'PY'
import json, os, urllib.request
ok = os.environ.get("JOB_STATUS") == "success"
repo = os.environ.get("REPO", "")
ref = os.environ.get("REF_NAME", "")
sha = os.environ.get("SHORT_SHA", "")
status = os.environ.get("JOB_STATUS", "")
ts = os.environ.get("DEPLOY_TS", "")
run_url = os.environ.get("RUN_URL", "")
rollback_reason = os.environ.get("ROLLBACK_REASON", "")
raw = os.environ.get("COMMIT_MSG", "") or ""
lines = [ln for ln in raw.splitlines() if ln.strip()]
msg = "\n".join(lines[:3]) if lines else "(无提交信息)"
if rollback_reason:
    msg = f"手动回滚原因：{rollback_reason}\n{msg}"
if len(msg) > 240:
    msg = msg[:240] + "…"
card = {
    "msg_type": "interactive",
    "card": {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green" if ok else "red",
            "title": {"tag": "plain_text",
                      "content": ("✅ 部署成功 · " if ok else "❌ 部署失败 · ") + repo},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**提交信息**\n{msg}"}},
            {"tag": "hr"},
            {"tag": "div", "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**分支**\n{ref}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**提交**\n`{sha}`"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**结果**\n{status}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**时间**\n{ts}"}},
            ]},
            {"tag": "action", "actions": [
                {"tag": "button", "type": "primary",
                 "text": {"tag": "plain_text", "content": "查看运行日志"},
                 "url": run_url}]},
            {"tag": "note", "elements": [
                {"tag": "plain_text", "content": "🤖 UptimeKuma · CI/CD 部署告警"}]},
        ],
    },
}
data = json.dumps(card).encode()
req = urllib.request.Request(
    os.environ["FEISHU_INFRA_WEBHOOK"], data=data,
    headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        print(f"飞书响应 http={r.status} body={r.read().decode()}")
except Exception as e:
    print(f"飞书通知失败(忽略): {e!r}")
PY
