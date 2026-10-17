#!/usr/bin/env python3
"""守住仓库忽略规则、README 产品图与现行入口文档的链接。"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
ENTRY_DOCUMENTS = (
    "README.md",
    "CLAUDE.md",
    "backend/CLAUDE.md",
    "frontend/CLAUDE.md",
    "backend/README.md",
    "frontend/README.md",
    "backend/CHAT_CORE_DATA_FLOW.md",
    "frontend/CHAT_UI_DATA_FLOW.md",
)


def read(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"缺少文件: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def is_ignored(relative: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", relative],
        cwd=ROOT,
        check=False,
    )
    return result.returncode == 0


def local_targets(pattern: str, content: str) -> list[str]:
    targets = []
    for target in re.findall(pattern, content):
        normalized = target.strip().strip("<>").split(maxsplit=1)[0]
        parsed = urlsplit(normalized)
        if parsed.scheme in {"http", "https", "mailto"} or not parsed.path:
            continue
        targets.append(unquote(parsed.path))
    return targets


class RepositoryDocsContractTest(unittest.TestCase):
    def test_root_ignore_policy_covers_generated_artifacts_without_hiding_all_databases(self) -> None:
        for relative in (
            ".DS_Store",
            "backend/.DS_Store",
            "backend/app.log",
            "frontend/error.log",
            "backend/fusion-test.db",
            "ops/__pycache__/module.pyc",
            ".env.local",
        ):
            self.assertTrue(is_ignored(relative), f"根忽略规则未覆盖生成文件: {relative}")
        self.assertFalse(is_ignored("backend/test/fixtures/contract.db"), "根忽略规则不得用 *.db 隐藏测试夹具")

    def test_generated_runtime_artifacts_are_not_tracked(self) -> None:
        generated = {"backend/.DS_Store", "backend/app.log", "backend/fusion-test.db", "frontend/error.log"}
        tracked = set(
            subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.split("\n")
        )
        self.assertEqual(generated & tracked, set(), "生成文件不得继续留在版本库索引中")

    def test_guidance_and_documentation_are_not_ignored(self) -> None:
        for relative in ("CLAUDE.md", "backend/CLAUDE.md", "frontend/CLAUDE.md", "backend/docs/FUTURE_DOCUMENT.md"):
            with self.subTest(relative=relative):
                self.assertFalse(is_ignored(relative), f"仓库文档不得被忽略: {relative}")

    def test_public_readme_has_three_local_product_visuals(self) -> None:
        readme = ROOT / "README.md"
        targets = local_targets(r"!\[[^\]]*\]\(([^)]+)\)", read(readme))
        self.assertGreaterEqual(len(targets), 3, "README.md 至少应包含三张本地产品图")
        missing = [target for target in targets if not (readme.parent / target).is_file()]
        self.assertEqual(missing, [], f"README.md 产品图不存在: {missing}")

    def test_entry_document_relative_links_exist(self) -> None:
        offenders: list[str] = []
        for relative in ENTRY_DOCUMENTS:
            document = ROOT / relative
            for target in local_targets(r"(?<!!)\[[^\]]*\]\(([^)]+)\)", read(document)):
                link = Path(target)
                if not (link if link.is_absolute() else document.parent / link).exists():
                    offenders.append(f"{relative}: {target}")
        self.assertEqual(offenders, [], f"现行入口含无效 Markdown 文件链接: {offenders}")


if __name__ == "__main__":
    unittest.main()
