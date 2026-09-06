"""PromptHub 发布验收；held 时以数据库目标和真实冻结结果验证本地保护。"""

import asyncio


def try_verify_held_bundle(preflight, verify_current):
    if preflight.get("state") != "held":
        return False
    from app.services.prompthub_sync_service import sync_prompthub_bundle

    result = asyncio.run(sync_prompthub_bundle())
    current = verify_current()
    if current.get("state") != "held":
        return False
    print(
        f"Prompt hold 验收通过: active={current['revision']} sync_status={result.get('status')}"
    )
    return True


if __name__ == "__main__":
    import asyncio
    import json
    import os
    import re
    import urllib.error
    import urllib.request

    from app.core.config import settings
    from app.db.database import SessionLocal

    raw_mode = settings.PROMPTHUB_SYNC_MODE
    mode = getattr(raw_mode, "value", raw_mode).strip().lower()
    if mode == "disabled":
        print("PromptHub bundle smoke skipped: sync mode is disabled")
        raise SystemExit(0)
    if mode not in {"shadow", "apply"}:
        raise SystemExit(f"unsupported PromptHub sync mode: {mode}")

    if try_verify_held_bundle(
        globals()["prompt_hold_preflight_result"],
        lambda: globals()["verify_prompt_hold_target"](SessionLocal),
    ):
        raise SystemExit(0)

    base_url = settings.PROMPTHUB_BASE_URL.rstrip("/")
    project_slug = settings.PROMPTHUB_PROJECT_SLUG
    api_key = settings.PROMPTHUB_API_KEY
    if not base_url or not project_slug or not api_key:
        raise SystemExit("PromptHub bundle smoke config is incomplete")

    url = f"{base_url}/api/v1/projects/by-slug/{project_slug}/prompts/published"
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=settings.PROMPTHUB_REQUEST_TIMEOUT_SECONDS,
        ) as response:
            status = response.status
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"PromptHub bundle smoke failed: HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"PromptHub bundle smoke failed: {exc.reason}") from None

    if status != 200 or body.get("code") != 0:
        raise SystemExit("PromptHub bundle smoke returned an invalid response")
    data = body.get("data")
    if not isinstance(data, dict):
        raise SystemExit("PromptHub bundle smoke data is missing")
    prompts = data.get("prompts")
    revision = data.get("revision")
    from app.core.prompt_catalog import PROMPT_SPECS

    if not isinstance(prompts, list) or len(prompts) != len(PROMPT_SPECS):
        count = len(prompts) if isinstance(prompts, list) else "invalid"
        raise SystemExit(f"PromptHub bundle smoke prompt count mismatch: {count}")
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{64}", revision) is None:
        raise SystemExit("PromptHub bundle smoke revision is invalid")

    from app.db.models import RuntimeConfigEntry
    from app.services.prompthub_sync_service import sync_prompthub_bundle

    try:
        from app.core.prompt_bundle_integrity import (
            PROMPT_BUNDLE_NAMESPACE,
            PROMPT_BUNDLE_STORAGE_KEY,
        )
    except ModuleNotFoundError:
        if os.environ.get("FUSION_ROLLBACK_REQUESTED") != "true":
            raise SystemExit("候选代码没有 v2 存储契约，拒绝以旧行通过验收")
        PROMPT_BUNDLE_NAMESPACE, PROMPT_BUNDLE_STORAGE_KEY = "prompt_bundle", "fusion"

    sync_result = asyncio.run(sync_prompthub_bundle())
    if (
        sync_result.get("status") != "success"
        or sync_result.get("revision") != revision
    ):
        raise SystemExit("PromptHub sync did not persist the fetched revision")

    session = SessionLocal()
    try:
        rows = (
            session.query(RuntimeConfigEntry)
            .filter(
                RuntimeConfigEntry.namespace == PROMPT_BUNDLE_NAMESPACE,
                RuntimeConfigEntry.key == PROMPT_BUNDLE_STORAGE_KEY,
            )
            .all()
        )
    finally:
        session.close()
    matching = [row for row in rows if row.version == revision]
    active = [row for row in rows if row.is_active]
    if len(matching) != 1:
        raise SystemExit("PromptHub synced revision is missing or duplicated")
    if mode == "shadow" and bool(matching[0].is_active) != bool(
        sync_result.get("active")
    ):
        raise SystemExit("PromptHub shadow 诊断与持久状态不一致")
    if mode == "apply" and (not matching[0].is_active or len(active) != 1):
        raise SystemExit("PromptHub apply revision was not atomically activated")
    if mode == "apply":
        from app.ai.prompts.defaults import DEFAULT_PROMPT_TEMPLATES
        from app.core.prompt_bundle import freeze_prompt_bundle

        frozen = freeze_prompt_bundle(DEFAULT_PROMPT_TEMPLATES)
        if frozen.source_kind != "prompthub_lkg" or frozen.source_revision != revision:
            raise SystemExit("真实冻结入口没有消费刚验收的完整 LKG")
    print(
        "PromptHub bundle smoke passed: "
        f"mode={mode} prompts={len(prompts)} revision={revision[:12]}... persisted=true"
    )
