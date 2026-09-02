#!/usr/bin/env python3
"""守住 Fusion monorepo 的文档、协作约定与 skill 发现合同。"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit


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
    ROOT / "backend/AGENTS.md",
    ROOT / "backend/CLAUDE.md",
    ROOT / "frontend/AGENTS.md",
    ROOT / "frontend/CLAUDE.md",
    ROOT / "docs/EXECUTION_LEDGER.md",
    ROOT / ".agents/skills/fusion-next-step/SKILL.md",
)
FORBIDDEN_OLD_PATHS = (
    "/Users/sean/code/fusion/" + "fusion-api",
    "/Users/sean/code/fusion/" + "fusion-ui",
    "../fusion-api",
    "../fusion-ui",
    "docs/superpowers",
)
GUIDANCE_START = "<!-- guidance-contract:start -->"
GUIDANCE_END = "<!-- guidance-contract:end -->"
MIGRATION_MARKER = ".github/scripts/test_repository_guidance_contract.py"
TASK5_ALLOWED_BUSINESS_PATHS = {
    "frontend/src/scripts/buildAndDeployWorkflow.test.ts",
}
EXPECTED_BACKEND_SPECS = {
    "2026-06-24-dynamic-network-search-design.md",
    "2026-06-24-network-diagnostics-design.md",
    "2026-06-28-agent-progress-protocol-v2-design.md",
    "2026-06-28-agent-run-continuation-design.md",
    "2026-06-30-search-read-planner-ledger-design.md",
    "2026-07-01-agent-tools-capability-design.md",
    "2026-07-01-evidence-ledger-used-source-design.md",
    "2026-07-01-model-catalog-audit-sync-design.md",
    "2026-07-01-multi-model-eval-v1-1-design.md",
    "2026-07-01-reasoning-tag-filter-design.md",
    "2026-07-01-search-failure-recovery-budget-v1-3-design.md",
    "2026-07-01-search-read-planner-v1-2-design.md",
    "2026-07-02-runtime-config-assets-design.md",
    "2026-07-02-runtime-config-governance-design.md",
    "2026-07-10-prompthub-migration-design.md",
    "2026-07-11-admin-audit-center-v1-design.md",
    "2026-07-12-admin-model-operations-v1-design.md",
    "2026-07-27-agent-plan-mode-v1-design.md",
    "2026-07-28-litellm-sustainable-governance-design.md",
    "2026-08-26-system-prompt-assembly.md",
    "2026-08-27-prompt-runtime-v2.md",
    "2026-08-27-run-capability-router.md",
    "2026-08-31-run-skills-mvp.md",
}
EXPECTED_FRONTEND_SPECS = {
    "2026-04-11-model-selector-redesign.md",
    "2026-06-22-agent-tool-process-ui-design.md",
    "2026-06-22-answer-evidence-layer-design.md",
    "2026-06-22-assistant-message-visual-hierarchy-design.md",
    "2026-06-22-web-chat-state-spine-design.md",
    "2026-06-23-chat-input-composer-polish-design.md",
    "2026-06-23-chat-message-structure-design.md",
    "2026-06-24-answer-evidence-sidebar-design.md",
    "2026-06-24-ui-ux-performance-polish.md",
    "2026-06-27-chat-new-route-design.md",
    "2026-06-28-agent-readable-timeline-design.md",
    "2026-07-03-conversation-files-v1-design.md",
    "2026-07-03-runtime-config-admin-entry-design.md",
    "2026-07-03-runtime-config-readability-design.md",
}
EXPECTED_FRONTEND_PLANS = {
    "2026-08-22-trajectory-p3.md",
    "2026-08-26-trajectory-display-regressions.md",
}
EXPECTED_FRONTEND_REPORTS = {
    "2026-06-24-ui-ux-performance-attribution.md",
    "trajectory-p3-verification.md",
}
EXPECTED_BACKEND_SKILLS = {
    "add-endpoint",
    "add-provider",
    "api-overview",
    "api-reference",
    "debug-stream",
    "dev-logs",
    "dev-test-api",
    "dev-verify",
}


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


def git_output(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "无错误输出"
        raise RuntimeError(f"无法解析 Task 5 Git 范围: git {' '.join(args)}: {detail}")
    return result.stdout.strip()


def resolve_commit(revision: str) -> str:
    if not revision or set(revision) == {"0"}:
        raise RuntimeError(f"无法解析 Task 5 Git 范围: 无效 revision {revision!r}")
    return git_output("rev-parse", "--verify", f"{revision}^{{commit}}")


def marker_exists(commit: str) -> bool:
    paths = git_output("ls-tree", "-r", "--name-only", commit, "--", MIGRATION_MARKER)
    return MIGRATION_MARKER in paths.splitlines()


def task5_range() -> tuple[str, str]:
    head = resolve_commit("HEAD")
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event == "pull_request":
        base_ref = os.environ.get("BASE_REF") or os.environ.get("GITHUB_BASE_REF")
        if not base_ref:
            raise RuntimeError("无法解析 Task 5 Git 范围: pull_request 缺少 BASE_REF")
        base_tip = resolve_commit(f"origin/{base_ref}")
        base = resolve_commit(git_output("merge-base", base_tip, head))
    elif event == "push":
        base = resolve_commit(os.environ.get("BEFORE_SHA", ""))
    elif event in ("", "workflow_dispatch"):
        base_tip = resolve_commit("origin/master")
        base = resolve_commit(git_output("merge-base", base_tip, head))
    else:
        raise RuntimeError(f"无法解析 Task 5 Git 范围: 不支持事件 {event!r}")
    return base, head


def task5_changed_paths() -> set[str]:
    """在迁移首次进入目标分支前审计完整 base..head，之后跳过一次性范围门禁。"""
    base, head = task5_range()
    if marker_exists(base) or not marker_exists(head):
        return set()
    return set(git_output("diff", "--name-only", base, head).splitlines())


def task5_business_path_violations(paths: set[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if (path.startswith("backend/app/") or path.startswith("frontend/src/"))
        and path not in TASK5_ALLOWED_BUSINESS_PATHS
    )


class Task5RangeHelperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        self.git("init", "-b", "master")
        self.git("config", "user.name", "Contract Test")
        self.git("config", "user.email", "contract@example.invalid")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def commit_files(self, message: str, files: dict[str, str]) -> str:
        for relative, content in files.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def changed_paths(self, **environment: str) -> set[str]:
        global ROOT
        original_root = ROOT
        original_environment = os.environ.copy()
        try:
            ROOT = self.repo
            os.environ.clear()
            os.environ.update(environment)
            return task5_changed_paths()
        finally:
            ROOT = original_root
            os.environ.clear()
            os.environ.update(original_environment)

    def test_current_task5_range_includes_changes_after_contract_introduction(self) -> None:
        base = self.commit_files("base", {"README.md": "base\n"})
        self.commit_files(
            "introduce contract",
            {".github/scripts/test_repository_guidance_contract.py": "marker\n"},
        )
        self.commit_files("later escape", {"backend/app/out_of_scope.py": "escaped = True\n"})

        paths = self.changed_paths(GITHUB_EVENT_NAME="push", BEFORE_SHA=base)

        self.assertIn("backend/app/out_of_scope.py", paths)

    def test_future_change_skips_one_time_task5_range_after_base_contains_contract(self) -> None:
        base = self.commit_files(
            "base with contract",
            {".github/scripts/test_repository_guidance_contract.py": "marker\n"},
        )
        self.git("update-ref", "refs/remotes/origin/master", base)
        self.commit_files("future feature", {"backend/app/future_feature.py": "future = True\n"})

        paths = self.changed_paths(GITHUB_EVENT_NAME="pull_request", BASE_REF="master")

        self.assertEqual(paths, set())

    def test_unresolvable_push_base_fails_closed(self) -> None:
        self.commit_files("base", {"README.md": "base\n"})
        self.commit_files(
            "introduce contract",
            {".github/scripts/test_repository_guidance_contract.py": "marker\n"},
        )

        with self.assertRaises(RuntimeError):
            self.changed_paths(GITHUB_EVENT_NAME="push", BEFORE_SHA="f" * 40)

    def test_business_path_validator_allows_only_the_reviewed_frontend_contract_test(self) -> None:
        validator = globals().get("task5_business_path_violations")
        self.assertIsNotNone(validator, "缺少 Task 5 业务目录范围 validator")
        if validator is None:
            return
        self.assertEqual(
            validator(
                {
                    "frontend/src/scripts/buildAndDeployWorkflow.test.ts",
                    "docs/EXECUTION_LEDGER.md",
                }
            ),
            [],
        )
        self.assertEqual(
            validator(
                {
                    "frontend/src/components/escaped.tsx",
                    "backend/app/escaped.py",
                }
            ),
            ["backend/app/escaped.py", "frontend/src/components/escaped.tsx"],
        )


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
        expected_documents = {
            "docs/specs/backend": EXPECTED_BACKEND_SPECS,
            "docs/specs/frontend": EXPECTED_FRONTEND_SPECS,
            "docs/implementation-plans/frontend": EXPECTED_FRONTEND_PLANS,
            "docs/reports/frontend": EXPECTED_FRONTEND_REPORTS,
        }
        for relative, expected in expected_documents.items():
            actual = {path.name for path in (ROOT / relative).glob("*.md")}
            self.assertEqual(
                expected - actual,
                set(),
                f"{relative} 缺少必要迁移文件: {sorted(expected - actual)}",
            )
        old_sources = (
            ROOT / "backend/docs/superpowers",
            ROOT / "frontend/docs/superpowers",
            ROOT / "backend/docs/implementation-plans",
        )
        self.assertEqual(
            [str(path.relative_to(ROOT)) for path in old_sources if path.exists()],
            [],
            "旧文档来源目录必须移除",
        )

    def test_root_agent_files_are_navigation_only(self) -> None:
        forbidden = ("app/", "src/", "uvicorn", "npm run dev", "docker-compose", "部署", "回滚")
        for relative in ("AGENTS.md", "CLAUDE.md"):
            content = read(ROOT / relative)
            for keyword in forbidden:
                self.assertNotIn(keyword, content, f"{relative} 不应承载应用执行规则: {keyword}")
            for target in ("backend/AGENTS.md", "frontend/AGENTS.md", "docs/EXECUTION_LEDGER.md"):
                self.assertIn(target, content)

    def test_public_readme_is_product_facing_not_agent_guidance(self) -> None:
        readme = ROOT / "README.md"
        self.assertNotIn(readme, GUIDANCE_FILES)
        content = read(readme)
        for entry in DISCOVERY_ENTRIES:
            self.assertNotIn(entry, content, f"README.md 不应承载 Agent 发现入口: {entry}")

    def test_public_readme_has_three_local_product_visuals(self) -> None:
        readme = ROOT / "README.md"
        targets = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", read(readme))
        local_targets = []
        for target in targets:
            normalized = target.strip().strip("<>").split(maxsplit=1)[0]
            parsed = urlsplit(normalized)
            if parsed.scheme in {"http", "https"} or not parsed.path:
                continue
            local_targets.append(normalized)
        self.assertGreaterEqual(len(local_targets), 3, "README.md 至少应包含三张本地产品图")
        missing = [target for target in local_targets if not (readme.parent / unquote(target)).is_file()]
        self.assertEqual(missing, [], f"README.md 产品图不存在: {missing}")

    def test_fusion_next_step_is_unique_and_uses_single_repo_discovery(self) -> None:
        copies = sorted(ROOT.glob("**/.agents/skills/fusion-next-step/SKILL.md"))
        self.assertEqual(copies, [ROOT / ".agents/skills/fusion-next-step/SKILL.md"])
        actual_backend_skills = {
            path.parent.name for path in (ROOT / "backend/.agents/skills").glob("*/SKILL.md")
        }
        self.assertEqual(
            EXPECTED_BACKEND_SKILLS - actual_backend_skills,
            set(),
            f"缺少预期 backend skills: {sorted(EXPECTED_BACKEND_SKILLS - actual_backend_skills)}",
        )
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
            for old_path in ("../" + "fusion-api/", "../" + "fusion-ui/"):
                if old_path in content:
                    offenders.append(f"{path.relative_to(ROOT)}: {old_path}")
        self.assertEqual(offenders, [], f"实施计划仍含旧兄弟仓路径: {offenders}")

    def test_current_entry_documents_have_no_old_absolute_checkout_paths(self) -> None:
        entries = (
            "README.md",
            "backend/README.md",
            "frontend/README.md",
            "backend/CHAT_CORE_DATA_FLOW.md",
            "frontend/CHAT_UI_DATA_FLOW.md",
            "backend/.codex/hooks.json",
            "backend/reports/trajectory-p0-baseline.md",
        )
        offenders: list[str] = []
        for relative in entries:
            content = read(ROOT / relative)
            for old_path in FORBIDDEN_OLD_PATHS[:2]:
                if old_path in content:
                    offenders.append(f"{relative}: {old_path}")
        self.assertEqual(offenders, [], f"现行入口仍含旧绝对 checkout 路径: {offenders}")

    def test_current_entry_markdown_relative_link_targets_exist(self) -> None:
        entries = (
            "README.md",
            "backend/README.md",
            "frontend/README.md",
            "backend/CHAT_CORE_DATA_FLOW.md",
            "frontend/CHAT_UI_DATA_FLOW.md",
        )
        offenders: list[str] = []
        for relative in entries:
            document = ROOT / relative
            for target in re.findall(r"(?<!!)\[[^\]]*\]\(([^)]+)\)", read(document)):
                normalized = target.strip().strip("<>").split(maxsplit=1)[0]
                parsed = urlsplit(normalized)
                if parsed.scheme in {"http", "https", "mailto"} or not parsed.path:
                    continue
                link_path = Path(unquote(parsed.path))
                resolved = link_path if link_path.is_absolute() else document.parent / link_path
                if not resolved.exists():
                    offenders.append(f"{relative}: {target}")
        self.assertEqual(offenders, [], f"现行入口含无效 Markdown 文件链接: {offenders}")

    def test_debug_stream_uses_only_payload_safe_stream_metadata_commands(self) -> None:
        content = read(ROOT / "backend/.agents/skills/debug-stream/SKILL.md")
        for command in ("XRANGE", "XREVRANGE", "XREAD", "XREADGROUP", "XINFO STREAM"):
            self.assertNotRegex(content, rf"(?i)\b{command}\b", f"debug-stream 不得读取 entry body: {command}")
        self.assertIn("XLEN", content, "debug-stream 应只读取 Stream 长度元数据")
        self.assertIn("不输出 entry body", content)

    def test_task5_introducing_change_does_not_touch_business_code(self) -> None:
        forbidden = task5_business_path_violations(task5_changed_paths())
        self.assertEqual(forbidden, [], f"Task 5 不得修改业务目录: {forbidden}")


if __name__ == "__main__":
    unittest.main()
