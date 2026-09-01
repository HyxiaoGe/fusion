#!/usr/bin/env python3
"""守住 Fusion monorepo 的文档、协作约定与 skill 发现合同。"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DISCOVERY_ENTRIES = (
    "docs/EXECUTION_LEDGER.md",
    "docs/implementation-plans",
    "docs/specs",
    "backend/docs/MODEL_ACCEPTANCE_RUNBOOK.md",
    "git log --oneline -40",
)
GUIDANCE_FILES = (
    ROOT / "AGENTS.md",
    ROOT / "CLAUDE.md",
    ROOT / "README.md",
    ROOT / "backend/AGENTS.md",
    ROOT / "backend/CLAUDE.md",
    ROOT / "frontend/AGENTS.md",
    ROOT / "frontend/CLAUDE.md",
    ROOT / "docs/EXECUTION_LEDGER.md",
    ROOT / ".agents/skills/fusion-next-step/SKILL.md",
)
FORBIDDEN_OLD_PATHS = (
    "/Users/sean/code/fusion/fusion-api",
    "/Users/sean/code/fusion/fusion-ui",
    "../fusion-api",
    "../fusion-ui",
    "docs/superpowers",
)
GUIDANCE_START = "<!-- guidance-contract:start -->"
GUIDANCE_END = "<!-- guidance-contract:end -->"


def read(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"缺少文件: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def guidance_contract(path: Path) -> str:
    content = read(path)
    if GUIDANCE_START not in content or GUIDANCE_END not in content:
        raise AssertionError(f"{path.relative_to(ROOT)} 缺少 guidance-contract 标记")
    start = content.index(GUIDANCE_START) + len(GUIDANCE_START)
    end = content.index(GUIDANCE_END, start)
    return content[start:end].strip()


def task5_changed_paths() -> set[str]:
    """读取引入本合同的提交；提交前则读取当前工作树。"""
    introducing = subprocess.run(
        [
            "git",
            "log",
            "--diff-filter=A",
            "--format=%H",
            "--",
            ".github/scripts/test_repository_guidance_contract.py",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if introducing:
        changed = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", introducing[-1]],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        return set(changed)

    tracked = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return set(tracked + untracked)


class RepositoryGuidanceContractTest(unittest.TestCase):
    def test_root_navigation_and_canonical_document_paths_exist(self) -> None:
        required = (
            ROOT / "AGENTS.md",
            ROOT / "CLAUDE.md",
            ROOT / "README.md",
            ROOT / "backend/AGENTS.md",
            ROOT / "backend/CLAUDE.md",
            ROOT / "frontend/AGENTS.md",
            ROOT / "frontend/CLAUDE.md",
            ROOT / "docs/EXECUTION_LEDGER.md",
            ROOT / "docs/implementation-plans",
            ROOT / "docs/specs/backend",
            ROOT / "docs/specs/frontend",
            ROOT / ".agents/skills/fusion-next-step/SKILL.md",
        )
        missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
        self.assertEqual(missing, [], f"缺少根导航或权威文档路径: {missing}")
        ledgers = sorted(path.relative_to(ROOT).as_posix() for path in ROOT.glob("**/EXECUTION_LEDGER.md"))
        self.assertEqual(ledgers, ["docs/EXECUTION_LEDGER.md"])
        expected_counts = {
            "docs/specs/backend": 23,
            "docs/specs/frontend": 14,
            "docs/implementation-plans/frontend": 2,
            "docs/reports/frontend": 2,
        }
        for relative, expected in expected_counts.items():
            actual = len(list((ROOT / relative).glob("*.md")))
            self.assertEqual(actual, expected, f"{relative} 文件数应为 {expected}，实际为 {actual}")

    def test_root_agent_files_are_navigation_only(self) -> None:
        forbidden = ("app/", "src/", "uvicorn", "npm run dev", "docker-compose", "部署", "回滚")
        for relative in ("AGENTS.md", "CLAUDE.md"):
            content = read(ROOT / relative)
            for keyword in forbidden:
                self.assertNotIn(keyword, content, f"{relative} 不应承载应用执行规则: {keyword}")
            for target in ("backend/AGENTS.md", "frontend/AGENTS.md", "docs/EXECUTION_LEDGER.md"):
                self.assertIn(target, content)

    def test_fusion_next_step_is_unique_and_uses_single_repo_discovery(self) -> None:
        skills = sorted(ROOT.glob("**/.agents/skills/*/SKILL.md"))
        self.assertEqual(len(skills), 9, f"最终应有 9 个独立 skill，实际为 {len(skills)}")
        copies = sorted(ROOT.glob("**/.agents/skills/fusion-next-step/SKILL.md"))
        self.assertEqual(copies, [ROOT / ".agents/skills/fusion-next-step/SKILL.md"])
        content = read(copies[0])
        self.assertIn("git rev-parse --show-toplevel", content)
        for entry in DISCOVERY_ENTRIES:
            self.assertIn(entry, content)
        for forbidden in FORBIDDEN_OLD_PATHS:
            self.assertNotIn(forbidden, content)

    def test_application_agent_and_claude_contracts_are_synonymous(self) -> None:
        required_phrases = (
            "不默认启动服务",
            "复用既有 Chrome 标签",
            "先定位根因",
            "按改动运行测试/构建",
            "部署/回滚需明确确认",
        ) + DISCOVERY_ENTRIES
        for application in ("backend", "frontend"):
            agents = ROOT / application / "AGENTS.md"
            claude = ROOT / application / "CLAUDE.md"
            self.assertEqual(guidance_contract(agents), guidance_contract(claude))
            contract = guidance_contract(agents)
            for phrase in required_phrases:
                self.assertIn(phrase, contract, f"{application} 缺少受控约定: {phrase}")

    def test_discovery_entrances_are_consistent_and_have_no_old_paths(self) -> None:
        for path in GUIDANCE_FILES:
            content = read(path)
            for entry in DISCOVERY_ENTRIES:
                self.assertIn(entry, content, f"{path.relative_to(ROOT)} 缺少发现入口: {entry}")
            for forbidden in FORBIDDEN_OLD_PATHS:
                self.assertNotIn(forbidden, content, f"{path.relative_to(ROOT)} 仍含旧路径: {forbidden}")

    def test_implementation_plans_do_not_reference_old_sibling_paths(self) -> None:
        offenders: list[str] = []
        for path in sorted((ROOT / "docs/implementation-plans").rglob("*.md")):
            content = read(path)
            for old_path in ("../fusion-api/", "../fusion-ui/"):
                if old_path in content:
                    offenders.append(f"{path.relative_to(ROOT)}: {old_path}")
        self.assertEqual(offenders, [], f"实施计划仍含旧兄弟仓路径: {offenders}")

    def test_task5_introducing_change_does_not_touch_business_code(self) -> None:
        forbidden = sorted(
            path
            for path in task5_changed_paths()
            if path.startswith("backend/app/") or path.startswith("frontend/src/")
        )
        self.assertEqual(forbidden, [], f"Task 5 不得修改业务目录: {forbidden}")


if __name__ == "__main__":
    unittest.main()
