"""P0 切换门禁 CLI：抓取部署前 effective map、生成基线 bundle、逐 key 字节校验。

用法::

    # 1. 抓取部署前 effective map 并生成基线 bundle（留存为「行为零变化」的事后证据）
    python scripts/verify_p0_effective_baseline.py capture --out baseline.json

    # 2. 发布该基线到 PromptHub 后，用待激活 bundle 逐 key 字节复核
    python scripts/verify_p0_effective_baseline.py verify --baseline baseline.json --candidate candidate.json

`verify` 不一致时以退出码 1 结束（fail closed），不得忽略后继续 apply。

candidate.json 需为 ``{"<catalog key>": "<正文>"}``，或 PromptHub published bundle
响应（``data.prompts[]`` 含 ``slug`` / ``content``）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.prompt_catalog import PROMPT_SPEC_BY_SLUG  # noqa: E402
from app.services.prompt_effective_map import (  # noqa: E402
    EffectiveEntry,
    build_effective_baseline_bundle,
    capture_pre_p0_effective_map,
    code_default_effective_revision,
    verify_bundle_matches_effective_map,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P0 effective baseline 门禁")
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture", help="抓取部署前 effective map 并生成基线 bundle")
    capture.add_argument("--out", required=True, help="基线 bundle 输出路径")

    verify = sub.add_parser("verify", help="逐 key UTF-8 字节校验待激活 bundle")
    verify.add_argument("--baseline", required=True, help="capture 产出的基线 bundle")
    verify.add_argument("--candidate", required=True, help="待激活 bundle")

    args = parser.parse_args(argv)
    if args.command == "capture":
        return _capture(Path(args.out))
    return _verify(Path(args.baseline), Path(args.candidate))


def _capture(out_path: Path) -> int:
    effective_map = capture_pre_p0_effective_map()
    payload = build_effective_baseline_bundle(effective_map)
    payload["code_default_effective_revision"] = code_default_effective_revision()
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已抓取 {len(payload['prompts'])} 项 effective map -> {out_path}")
    for entry in payload["prompts"]:
        print(f"  {entry['slug']}: source={entry['captured_source']} version={entry['captured_version']}")
    print("注意：本文件是「行为零变化」的事后可核对证据，必须留存。")
    return 0


def _verify(baseline_path: Path, candidate_path: Path) -> int:
    effective_map = _load_baseline(baseline_path)
    candidate = _load_candidate(candidate_path)
    mismatches = verify_bundle_matches_effective_map(candidate, effective_map)
    if mismatches:
        print("字节校验失败，禁止进入 apply：", file=sys.stderr)
        for item in mismatches:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(f"逐 key UTF-8 字节校验通过（{len(effective_map)} 项），可进入 apply。")
    return 0


def _load_baseline(path: Path) -> dict[str, EffectiveEntry]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries: dict[str, EffectiveEntry] = {}
    for item in payload["prompts"]:
        spec = PROMPT_SPEC_BY_SLUG[item["slug"]]
        entries[spec.key] = EffectiveEntry(
            key=spec.key,
            slug=spec.slug,
            content=item["content"],
            variables=spec.variables,
            source=item.get("captured_source", "unknown"),
            source_version=item.get("captured_version", "unknown"),
        )
    return entries


def _load_candidate(path: Path) -> dict[str, str]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    prompts = _published_bundle_prompts(payload)
    if prompts is not None:
        return prompts
    if isinstance(payload, dict) and all(isinstance(value, str) for value in payload.values()):
        return dict(payload)
    raise SystemExit("无法解析 candidate：需为 {key: 正文} 或 PromptHub published bundle 响应")


def _published_bundle_prompts(payload: Any) -> dict[str, str] | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    items = data.get("prompts") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    prompts: dict[str, str] = {}
    for item in items:
        slug = item.get("slug")
        spec = PROMPT_SPEC_BY_SLUG.get(slug)
        if spec is None:
            # 未知 slug 交给字节校验报告，不在解析阶段静默丢弃。
            prompts[str(slug)] = str(item.get("content", ""))
            continue
        prompts[spec.key] = str(item.get("content", ""))
    return prompts


if __name__ == "__main__":
    raise SystemExit(main())
