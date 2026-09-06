"""旧服务继续消费 v1 时预置 v2，不修改旧正文或历史 Run。"""

import copy
from unittest.mock import patch

import pytest
from sqlalchemy import event

from app.core.prompt_bundle import _load_active_bundle_payload, validate_published_bundle
from test import test_prompt_bundle_v2_transactions as transactions
from test.test_prompt_bundle_v2_integrity import published_v2_fixture
from test.test_prompt_bundle_v2_transactions import add_bundle, load_rows

session_factory = transactions.session_factory


def bridge_inputs(factory):
    bundle = published_v2_fixture()
    source = validate_published_bundle(bundle)
    legacy = {
        "schema_version": 1,
        "project_slug": source["project_slug"],
        "revision": source["revision"],
        "prompts": {
            key: {
                name: item[name]
                for name in ("slug", "version", "content", "variables", "content_sha256", "published_at")
            }
            for key, item in source["prompts"].items()
        },
    }
    old_id = add_bundle(factory, legacy, key="fusion")
    baseline = {key: item["content"] for key, item in legacy["prompts"].items()}
    return bundle, old_id, legacy, baseline


def seed(factory, bundle, baseline, revision):
    from app.services.prompt_bundle_bridge import seed_verified_bundle

    with patch("app.services.prompt_effective_map.settings.PROMPT_P0_BASELINE_ATTESTED", True):
        return seed_verified_bundle(
            bundle,
            baseline=baseline,
            expected_legacy_revision=revision,
            actor="测试操作员",
            reason="完整包预置",
            session_factory=factory,
        )


def test_seed_preserves_old_reader_and_makes_new_reader_ready(session_factory):
    bundle, old_id, legacy, baseline = bridge_inputs(session_factory)
    result = seed(session_factory, bundle, baseline, legacy["revision"])
    after = load_rows(session_factory)
    assert after[old_id].is_active
    assert after[old_id].payload == legacy
    ready = _load_active_bundle_payload(session_factory=session_factory, use_cache=False)
    assert ready is not None
    assert ready["schema_version"] == 2
    assert {key: item["content"] for key, item in ready["prompts"].items()} == baseline
    assert result["revision"] == bundle.revision
    assert result["status"] == "seeded"
    receipts = [row for row in after.values() if row.key == "fusion:v2:bridge"]
    assert len(receipts) == 1
    assert receipts[0].payload["actor"] == "测试操作员"
    assert receipts[0].payload["legacy_revision"] == legacy["revision"]


@pytest.mark.parametrize("failure", ["baseline", "revision", "commit"])
def test_seed_failure_preserves_old_service_and_does_not_leave_half_v2(session_factory, failure):
    from app.services.prompt_bundle_bridge import PromptBundleBridgeError

    bundle, old_id, legacy, baseline = bridge_inputs(session_factory)
    if failure == "baseline":
        baseline["limit_summary"] += "\n"
    expected = "a" * 64 if failure == "revision" else legacy["revision"]

    def fail_commit(session):
        raise PromptBundleBridgeError("注入提交失败")

    if failure == "commit":
        event.listen(session_factory, "before_commit", fail_commit)
    try:
        with pytest.raises(PromptBundleBridgeError):
            seed(session_factory, bundle, baseline, expected)
    finally:
        if failure == "commit":
            event.remove(session_factory, "before_commit", fail_commit)
    after = load_rows(session_factory)
    assert set(after) == {old_id}
    assert after[old_id].is_active
    assert after[old_id].payload == legacy


def test_old_sync_cannot_cancel_v2_and_seed_is_idempotent(session_factory):
    bundle, old_id, legacy, baseline = bridge_inputs(session_factory)
    seed(session_factory, bundle, baseline, legacy["revision"])
    second = seed(session_factory, bundle, baseline, legacy["revision"])
    assert second["status"] == "already_seeded"
    before = _load_active_bundle_payload(session_factory=session_factory, use_cache=False)
    from app.db.models import RuntimeConfigEntry

    with session_factory() as session:
        old_rows = session.query(RuntimeConfigEntry).filter_by(namespace="prompt_bundle", key="fusion").all()
        for row in old_rows:
            row.is_active = False
        replacement = copy.deepcopy(legacy)
        replacement["revision"] = "a" * 64
        session.add(
            RuntimeConfigEntry(
                namespace="prompt_bundle", key="fusion", version="a" * 64, payload=replacement, is_active=True
            )
        )
        session.commit()
    after = _load_active_bundle_payload(session_factory=session_factory, use_cache=False)
    assert before == after


def test_retire_preserves_v1_payload_and_requires_ready_v2_revision(session_factory):
    from app.services.prompt_bundle_bridge import PromptBundleBridgeError, retire_legacy_bundle

    bundle, old_id, legacy, baseline = bridge_inputs(session_factory)
    with pytest.raises(PromptBundleBridgeError):
        retire_legacy_bundle(
            expected_v2_revision=bundle.revision,
            actor="测试操作员",
            reason="旧 worker 已退出",
            session_factory=session_factory,
        )
    seed(session_factory, bundle, baseline, legacy["revision"])
    with patch("app.services.prompt_effective_map.settings.PROMPT_P0_BASELINE_ATTESTED", True):
        result = retire_legacy_bundle(
            expected_v2_revision=bundle.revision,
            actor="测试操作员",
            reason="旧 worker 已退出",
            session_factory=session_factory,
        )
    after = load_rows(session_factory)
    assert result["retired"] == 1
    assert not after[old_id].is_active
    assert after[old_id].payload == legacy
    assert _load_active_bundle_payload(session_factory=session_factory, use_cache=False) is not None
