import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("detect_changes.py")
SPEC = importlib.util.spec_from_file_location("detect_changes", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules["detect_changes"] = MODULE
SPEC.loader.exec_module(MODULE)


class DetectChangesTests(unittest.TestCase):
    def test_backend_only(self) -> None:
        self.assertEqual(
            MODULE.classify(["backend/app/main.py"]),
            {"api": True, "ui": False, "shared": False},
        )

    def test_frontend_only_does_not_depend_on_backend(self) -> None:
        self.assertEqual(
            MODULE.classify(["frontend/src/app.tsx"]),
            {"api": False, "ui": True, "shared": False},
        )

    def test_root_ci_change_runs_both(self) -> None:
        self.assertEqual(
            MODULE.classify([".github/workflows/pr-ci.yml"]),
            {"api": True, "ui": True, "shared": True},
        )

    def test_pull_request_uses_merge_base(self) -> None:
        diff_range = MODULE.select_diff_range(
            event_name="pull_request",
            base_ref="master",
            head="abc",
        )
        self.assertEqual(diff_range, MODULE.DiffRange("merge-base", "origin/master", "abc"))

    def test_push_uses_before_and_head_including_merge_commit(self) -> None:
        diff_range = MODULE.select_diff_range(
            event_name="push",
            before="a" * 40,
            head="b" * 40,
        )
        self.assertEqual(diff_range, MODULE.DiffRange("range", "a" * 40, "b" * 40))

    def test_initial_push_uses_root_diff(self) -> None:
        diff_range = MODULE.select_diff_range(
            event_name="push",
            before=MODULE.ZERO_SHA,
            head="b" * 40,
        )
        self.assertEqual(diff_range, MODULE.DiffRange("initial-push", None, "b" * 40))

    def test_manual_run_honors_explicit_base_and_head(self) -> None:
        diff_range = MODULE.select_diff_range(
            event_name="workflow_dispatch",
            head="ignored",
            dispatch_base="a" * 40,
            dispatch_head="b" * 40,
        )
        self.assertEqual(diff_range, MODULE.DiffRange("range", "a" * 40, "b" * 40))


if __name__ == "__main__":
    unittest.main()
