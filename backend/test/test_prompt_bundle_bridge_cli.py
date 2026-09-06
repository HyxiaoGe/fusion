"""部署入口在任何写入前验证完整基线，默认不执行数据库变更。"""

import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from test.test_prompt_bundle_v2_integrity import published_v2_fixture


def load_cli():
    path = Path(__file__).resolve().parents[1] / "scripts" / "bridge_prompt_bundle_v2.py"
    spec = importlib.util.spec_from_file_location("bridge_prompt_bundle_v2", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def baseline_file(tmp_path, bundle):
    import hashlib

    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(
            {
                "project_slug": "fusion",
                "source_kind": "prompthub_lkg",
                "revision": bundle.revision,
                "prompts": {
                    item.slug.replace("-", "_"): {
                        "content": item.content,
                        "content_sha256": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                    }
                    for item in bundle.prompts
                },
            },
            ensure_ascii=False,
        )
    )
    return path


def test_dry_run_fetches_and_validates_without_seeding(tmp_path):
    cli = load_cli()
    bundle = published_v2_fixture()
    path = baseline_file(tmp_path, bundle)
    with (
        patch.object(cli.settings, "PROMPTHUB_SYNC_MODE", "apply"),
        patch.object(cli.settings, "PROMPT_P0_BASELINE_ATTESTED", True),
        patch.object(cli, "load_stored_active_bundle_payload", return_value=None),
        patch.object(cli, "fetch_bundle", new=AsyncMock(return_value=bundle)),
        patch.object(cli, "seed_verified_bundle") as seed,
    ):
        assert cli.main(["prepare", "--baseline", str(path), "--actor", "测试", "--reason", "测试"]) == 0
    seed.assert_not_called()


def test_tampered_baseline_fails_before_fetch_or_seed(tmp_path):
    cli = load_cli()
    bundle = published_v2_fixture()
    path = baseline_file(tmp_path, bundle)
    material = json.loads(path.read_text())
    material["prompts"]["limit_summary"]["content"] += "\n"
    path.write_text(json.dumps(material))
    with (
        patch.object(cli.settings, "PROMPTHUB_SYNC_MODE", "apply"),
        patch.object(cli, "fetch_bundle", new=AsyncMock()) as fetch,
        patch.object(cli, "seed_verified_bundle") as seed,
        pytest.raises(ValueError, match="摘要"),
    ):
        cli.main(["prepare", "--baseline", str(path), "--actor", "测试", "--reason", "测试", "--apply"])
    fetch.assert_not_called()
    seed.assert_not_called()


def test_existing_v2_is_checked_without_fetching_or_reseeding(tmp_path):
    from app.core.prompt_bundle import validate_published_bundle

    cli = load_cli()
    bundle = published_v2_fixture()
    path = baseline_file(tmp_path, bundle)
    with (
        patch.object(cli.settings, "PROMPTHUB_SYNC_MODE", "apply"),
        patch.object(cli.settings, "PROMPT_P0_BASELINE_ATTESTED", True),
        patch.object(cli, "load_stored_active_bundle_payload", return_value=validate_published_bundle(bundle)),
        patch.object(cli, "fetch_bundle", new=AsyncMock()) as fetch,
        patch.object(cli, "seed_verified_bundle") as seed,
    ):
        assert cli.main(["prepare", "--baseline", str(path), "--actor", "测试", "--reason", "测试", "--apply"]) == 0
    fetch.assert_not_called()
    seed.assert_not_called()
