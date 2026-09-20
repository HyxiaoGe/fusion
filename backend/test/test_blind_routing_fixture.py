"""盲测集结构与原始数据守卫，不执行或评分能力分类。"""

import hashlib
import json
import unittest
from pathlib import Path

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "blind_routing_probe.json"

_ORIGINAL_CASE_IDS = frozenset(
    {
        "abstract-01",
        "abstract-02",
        "abstract-03",
        "abstract-04",
        "abstract-05",
        "boundary-01",
        "boundary-02",
        "boundary-03",
        "direct-01",
        "direct-02",
        "direct-03",
        "direct-04",
        "place-01",
        "place-02",
        "transform-01",
        "transform-02",
        "travel-01",
        "travel-02",
        "travel-03",
        "travel-04",
        "travel-05",
        "travel-06",
        "travel-07",
        "travel-08",
        "travel-09",
        "travel-10",
        "weather-01",
        "weather-02",
        "weather-03",
        "web-01",
        "web-02",
        "web-03",
        "web-04",
    }
)
_PROBE_GROUPS = frozenset(
    {
        "travel",
        "weather",
        "place",
        "direct",
        "transform",
        "web",
        "abstract",
        "boundary",
        "identity",
        "mcp_alias",
        "verify_verb",
    }
)


class BlindRoutingFixtureContractTests(unittest.TestCase):
    """只校验 fixture 结构和旧数据，通过 unittest discover 进入现有 CI。"""

    def test_original_blind_probe_cases_are_unchanged(self):
        payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        original = [case for case in payload["cases"] if case["id"] in _ORIGINAL_CASE_IDS]
        self.assertEqual(len(original), len(_ORIGINAL_CASE_IDS))
        self.assertEqual({case["id"] for case in original}, _ORIGINAL_CASE_IDS)
        guarded = {
            "description": payload["description"],
            "acceptable_packages_note": payload["acceptable_packages_note"],
            "cases": sorted(original, key=lambda case: case["id"]),
        }
        digest = hashlib.sha256(json.dumps(guarded, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.assertEqual(digest, "26fde023d0a8247bc2624b12d55bb8fc7a37e74599b38882f9aa5002be39af2a")

    def test_full_fixture_schema_is_wellformed(self):
        payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        cases = payload["cases"]
        self.assertIsInstance(cases, list)
        self.assertTrue(cases)
        ids = []
        for case in cases:
            with self.subTest(case_id=case.get("id") if isinstance(case, dict) else None):
                self.assertIsInstance(case, dict)
                for field in ("id", "question", "group"):
                    self.assertIsInstance(case[field], str)
                    self.assertTrue(case[field].strip())
                ids.append(case["id"])
                self.assertIn(case["group"], _PROBE_GROUPS)
                if "expected_layer" in case:
                    self.assertIsInstance(case["expected_layer"], str)
                    self.assertIn(case["expected_layer"], {"literal", "model"})
                packages = case["acceptable_packages"]
                self.assertIsInstance(packages, list)
                self.assertTrue(packages)
                for package in packages:
                    self.assertIsInstance(package, str)
                    self.assertTrue(package.strip())
                if "available_tools" in case:
                    self.assertIsInstance(case["available_tools"], list)
                    for tool in case["available_tools"]:
                        self.assertIsInstance(tool, str)
                        self.assertTrue(tool.strip())
        self.assertEqual(len(ids), len(set(ids)))
