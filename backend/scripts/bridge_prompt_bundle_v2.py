"""候选镜像的部署前 v2 预置入口；默认 dry-run，不启动应用生命周期。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.prompt_bundle import load_stored_active_bundle_payload, validate_published_bundle  # noqa: E402
from app.core.prompt_catalog import PROMPT_SPEC_BY_KEY  # noqa: E402
from app.services.prompt_bundle_bridge import _assert_same_contents, seed_verified_bundle  # noqa: E402
from app.services.prompt_effective_map import assert_payload_p0_gate  # noqa: E402
from app.services.prompthub_sync_service import _build_client  # noqa: E402


async def fetch_bundle():
    return await _build_client().fetch_published_bundle()


def load_baseline(path: Path):
    material = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(material, dict)
        or material.get("project_slug") != settings.PROMPTHUB_PROJECT_SLUG
        or material.get("source_kind") != "prompthub_lkg"
        or not isinstance(material.get("revision"), str)
        or re.fullmatch(r"[0-9a-f]{64}", material["revision"]) is None
    ):
        raise ValueError("部署前基线缺少明确的 LKG 来源身份")
    prompts = material.get("prompts")
    if not isinstance(prompts, dict) or set(prompts) != set(PROMPT_SPEC_BY_KEY):
        raise ValueError("部署前基线未完整覆盖 catalog")
    contents = {}
    for key, item in prompts.items():
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            raise ValueError(f"部署前基线正文无效：{key}")
        content = item["content"]
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != item.get("content_sha256"):
            raise ValueError(f"部署前基线正文摘要不一致：{key}")
        contents[key] = content
    return material["revision"], contents


def prepare(args):
    if settings.PROMPTHUB_SYNC_MODE != "apply":
        return {"status": "skipped", "reason": "当前不是 apply 模式"}
    revision, baseline = load_baseline(Path(args.baseline))
    if not settings.PROMPT_P0_BASELINE_ATTESTED:
        raise ValueError("v2 预置要求 P0 基线已 attested")
    existing = load_stored_active_bundle_payload()
    if existing is not None:
        contents = {key: item["content"] for key, item in existing["prompts"].items()}
        _assert_same_contents(contents, baseline)
        assert_payload_p0_gate(existing)
        return {"status": "v2_ready", "revision": existing["revision"]}
    bundle = asyncio.run(fetch_bundle())
    payload = validate_published_bundle(bundle)
    contents = {key: item["content"] for key, item in payload["prompts"].items()}
    _assert_same_contents(contents, baseline)
    assert_payload_p0_gate(payload)
    if not args.apply:
        return {"status": "validated", "revision": payload["revision"], "mode": "dry-run"}
    return seed_verified_bundle(
        bundle,
        baseline=baseline,
        expected_legacy_revision=revision,
        actor=args.actor,
        reason=args.reason,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="完整 v2 Prompt bundle 部署桥接")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("prepare", help="启动新服务前验证或预置完整 v2")
    command.add_argument("--baseline", required=True, help="当前服务实际冻结输出的完整基线文件")
    command.add_argument("--actor", required=True)
    command.add_argument("--reason", required=True)
    command.add_argument("--apply", action="store_true", help="显式执行 v2 预置；不写 PromptHub")
    args = parser.parse_args(argv)
    result = prepare(args)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
