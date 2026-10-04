import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("detect_changes.py")
SPEC = importlib.util.spec_from_file_location("detect_changes", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules["detect_changes"] = MODULE
SPEC.loader.exec_module(MODULE)


class DetectChangesTests(unittest.TestCase):
    def test_backend_only(self) -> None:
        self.assertEqual(
            MODULE.classify(["backend/app/db/models.py"]),
            {"api": True, "ui": False, "shared": False, "routing_eval": False, "agent_eval": False},
        )

    def test_frontend_only_does_not_depend_on_backend(self) -> None:
        self.assertEqual(
            MODULE.classify(["frontend/src/app.tsx"]),
            {"api": False, "ui": True, "shared": False, "routing_eval": False, "agent_eval": False},
        )

    def test_root_ci_change_runs_both(self) -> None:
        self.assertEqual(
            MODULE.classify([".github/workflows/pr-ci.yml"]),
            {"api": True, "ui": True, "shared": True, "routing_eval": False, "agent_eval": False},
        )

    def test_classifier_changes_request_routing_eval(self) -> None:
        for path in (
            "backend/app/services/stream/run_capability_model_classifier.py",
            "backend/app/ai/prompts/runtime_prompts.toml",
            "backend/test/fixtures/blind_routing_probe.json",
        ):
            with self.subTest(path=path):
                self.assertTrue(MODULE.classify([path])["routing_eval"])
        self.assertFalse(MODULE.classify(["backend/app/services/stream/runner.py"])["routing_eval"])

    def test_agent_behavior_changes_request_agent_eval(self) -> None:
        for path in (
            "backend/app/services/stream/runner.py",
            "backend/app/services/chat_service.py",
            "backend/app/services/tool_handlers/weather.py",
            "backend/app/ai/skills/trip-planning/SKILL.md",
            "backend/app/ai/prompts/runtime_prompts.toml",
            "backend/evals/cases/escalation.yaml",
            "backend/app/evals/checks.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(MODULE.classify([path])["agent_eval"])
        for path in ("backend/app/api/admin_audit.py", "backend/test/test_eval_checks.py", "frontend/src/app.tsx"):
            with self.subTest(path=path):
                self.assertFalse(MODULE.classify([path])["agent_eval"])

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

    def test_initial_push_classifies_the_complete_tree(self) -> None:
        diff_range = MODULE.DiffRange("initial-push", None, "b" * 40)
        with patch.object(
            MODULE.subprocess,
            "check_output",
            return_value=b"backend/main.py\0frontend/package.json\0",
        ) as check_output:
            self.assertEqual(
                MODULE.changed_paths(diff_range),
                ["backend/main.py", "frontend/package.json"],
            )
        check_output.assert_called_once_with(
            ["git", "ls-tree", "--name-only", "-r", "-z", "b" * 40]
        )

    def test_manual_run_validates_complete_current_tree(self) -> None:
        diff_range = MODULE.select_diff_range(
            event_name="workflow_dispatch",
            head="b" * 40,
        )
        self.assertEqual(diff_range, MODULE.DiffRange("initial-push", None, "b" * 40))


if __name__ == "__main__":
    unittest.main()
