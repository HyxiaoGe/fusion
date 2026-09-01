#!/usr/bin/env python3
"""为 Fusion monorepo CI 计算可靠的 diff 范围和应用变更集合。"""

from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


ZERO_SHA = "0" * 40


@dataclass(frozen=True)
class DiffRange:
    mode: str
    base: str | None
    head: str


def select_diff_range(
    *,
    event_name: str,
    head: str,
    before: str = "",
    base_ref: str = "",
    dispatch_base: str = "",
    dispatch_head: str = "",
) -> DiffRange:
    if event_name == "pull_request":
        if not base_ref:
            raise ValueError("pull_request 缺少 base_ref")
        return DiffRange("merge-base", f"origin/{base_ref}", head)
    if event_name == "push":
        if not before or before == ZERO_SHA:
            return DiffRange("initial-push", None, head)
        return DiffRange("range", before, head)
    if event_name == "workflow_dispatch":
        resolved_head = dispatch_head or head
        return DiffRange("range", dispatch_base or f"{resolved_head}^", resolved_head)
    raise ValueError(f"不支持的事件: {event_name}")


def changed_paths(diff_range: DiffRange) -> list[str]:
    if diff_range.mode == "initial-push":
        command = [
            "git",
            "ls-tree",
            "--name-only",
            "-r",
            "-z",
            diff_range.head,
        ]
    else:
        assert diff_range.base is not None
        base = diff_range.base
        if diff_range.mode == "merge-base":
            base = subprocess.check_output(
                ["git", "merge-base", diff_range.base, diff_range.head],
                text=True,
            ).strip()
        command = ["git", "diff", "--name-only", "-z", base, diff_range.head]
    output = subprocess.check_output(command)
    return [path.decode("utf-8") for path in output.split(b"\0") if path]


def classify(paths: list[str]) -> dict[str, bool]:
    backend_changed = any(path == "backend" or path.startswith("backend/") for path in paths)
    frontend_changed = any(path == "frontend" or path.startswith("frontend/") for path in paths)
    shared_changed = any(
        not (
            path == "backend"
            or path.startswith("backend/")
            or path == "frontend"
            or path.startswith("frontend/")
        )
        for path in paths
    )
    return {
        "api": backend_changed or shared_changed,
        "ui": frontend_changed or shared_changed,
        "shared": shared_changed,
    }


def write_outputs(values: dict[str, str | bool]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{key}={str(value).lower() if isinstance(value, bool) else value}" for key, value in values.items()]
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--before", default="")
    parser.add_argument("--base-ref", default="")
    parser.add_argument("--dispatch-base", default="")
    parser.add_argument("--dispatch-head", default="")
    args = parser.parse_args()

    diff_range = select_diff_range(
        event_name=args.event_name,
        head=args.head,
        before=args.before,
        base_ref=args.base_ref,
        dispatch_base=args.dispatch_base,
        dispatch_head=args.dispatch_head,
    )
    paths = changed_paths(diff_range)
    result = classify(paths)
    write_outputs(
        {
            **result,
            "base": diff_range.base or "<empty-tree>",
            "head": diff_range.head,
            "mode": diff_range.mode,
        }
    )


if __name__ == "__main__":
    main()
