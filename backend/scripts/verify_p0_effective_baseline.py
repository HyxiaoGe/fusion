"""P0 切换门禁 CLI：抓取部署前 effective map、生成基线 bundle、逐 key 字节校验。

用法::

    # 1. 抓取部署前 effective map（必须在切模式/换镜像**之前**执行；
    #    --sync-mode 填目标环境当时的 PROMPTHUB_SYNC_MODE）
    python scripts/verify_p0_effective_baseline.py capture --sync-mode apply --out baseline.json

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
    diff_effective_maps,
    resolve_effective_map_with_current_code,
    verify_bundle_matches_effective_map,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P0 effective baseline 门禁")
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture", help="抓取部署前 effective map 并生成基线 bundle")
    capture.add_argument("--out", required=True, help="基线 bundle 输出路径")
    capture.add_argument(
        "--sync-mode",
        required=True,
        choices=("apply", "shadow", "disabled"),
        help="被抓取环境**当时**的 PROMPTHUB_SYNC_MODE。必须显式给出：抓取要在切模式/"
        "换镜像之前完成，本进程的设置未必等于目标环境的设置，写错会抓到错误的基线。",
    )

    verify = sub.add_parser("verify", help="逐 key UTF-8 字节校验待激活 bundle")
    verify.add_argument("--baseline", required=True, help="capture 产出的基线 bundle")
    verify.add_argument("--candidate", required=True, help="待激活 bundle")

    preflight = sub.add_parser(
        "preflight",
        help="迁移安全判据：比对部署前 effective map 与候选代码在当前配置下的实际解析结果",
    )
    preflight.add_argument("--baseline", required=True, help="capture 产出的基线 bundle")

    args = parser.parse_args(argv)
    if args.command == "capture":
        return _capture(Path(args.out), sync_mode=args.sync_mode)
    if args.command == "preflight":
        return _preflight(Path(args.baseline))
    return _verify(Path(args.baseline), Path(args.candidate))


def _preflight(baseline_path: Path) -> int:
    """安全判据只能是逐 key 字节相等；captured_source 不能承担这个判据。"""

    baseline = _load_baseline(baseline_path)
    candidate = resolve_effective_map_with_current_code()
    mismatches = diff_effective_maps(baseline, candidate)
    if mismatches:
        print("迁移前置校验失败，按此配置部署会改变模型可见正文：", file=sys.stderr)
        for item in mismatches:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(f"迁移前置校验通过（{len(baseline)} 项逐 key 字节相等），按当前配置部署不会改变正文。")
    for key in sorted(candidate):
        print(f"  {key}: source={candidate[key].source}")
    return 0


def _capture(out_path: Path, *, sync_mode: str) -> int:
    if sync_mode != "apply":
        print(
            f"注意：按 sync_mode={sync_mode} 抓取，P0 之前不会命中 active bundle，"
            "已消费项将记为 legacy / 代码默认值。若目标环境实际是 apply，请改用 --sync-mode apply。",
            file=sys.stderr,
        )
    effective_map = capture_pre_p0_effective_map(sync_mode=sync_mode)
    payload = build_effective_baseline_bundle(effective_map)
    payload["code_default_effective_revision"] = code_default_effective_revision()
    payload["captured_sync_mode"] = sync_mode
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已抓取 {len(payload['prompts'])} 项 effective map（sync_mode={sync_mode}）-> {out_path}")
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
