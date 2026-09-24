"""#107 六条「身份介绍 + 产品任务」复合请求的协议回放覆盖。

这个文件补的是 #132 留下的覆盖空洞：`test_identity_model_delegation.py` 在 `51db0c8d`
被删除（它断言的是已删除的身份字面层行为），而 `identity_classifier_observed_failures.json`
里那六条原句从此无人读取——#107 追踪的缺陷变成了一条测试都没有。

**这里断言的不是模型正确率。** 模型响应全部来自 fixture 里 2026-09-20 实测记录的原始内容，
测试只回放协议：请求有没有被模型之前的某一层截走、记录的响应会解析成什么、以及期望的业务
能力包在当前契约下是否可达。真实模型对这六条的分类要在 dev 容器按 #107 的验收口径成对取数，
不由本文件断言。

写成 `unittest.TestCase` 是有意的：CI 的后端测试主体是 `unittest discover`，它只收集
`TestCase` 子类，顶层 pytest 函数一条都不收（见 #143）。用 TestCase 才能让这六条真的在 CI 里跑。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.stream.run_capability_model_classifier import classify_capability_request_with_model

_FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "identity_classifier_observed_failures.json"

_ALL_TOOLS = [
    "web_search",
    "url_read",
    "weather_forecast",
    "local_place_search",
    "route_compare",
    "search_flights",
    "search_trains",
]

# 业务期望包 -> 该包在契约里的工具集合。模型答对时应当返回这一对。
_EXPECTED_PACKAGE_TOOLS = {
    "weather": ["weather_forecast"],
    "place_discovery": ["local_place_search"],
}


def _response(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _load_cases() -> list[dict]:
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    return payload["cases"]


class IdentityCompoundDelegationTests(unittest.TestCase):
    """#107 六条复合请求：不被提前截走、记录响应可回放、业务期望包契约可达。"""

    def setUp(self) -> None:
        self.cases = _load_cases()
        self.assertEqual(len(self.cases), 6, "fixture 应当保留 #107 的全部六条原句")
        patcher = patch(
            "app.services.stream.run_capability_model_classifier.settings.LITELLM_API_KEY",
            "test-key",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _classify(self, question: str, response_content: str) -> tuple[object, list]:
        """用给定的模型响应内容跑一次分类，返回候选与 (result, error_type) 记录。"""

        observed: list[tuple[str, str | None]] = []
        with patch(
            "app.services.stream.run_capability_model_classifier.litellm.completion",
            return_value=_response(response_content),
        ) as completion:
            candidate = classify_capability_request_with_model(
                question,
                _ALL_TOOLS,
                result_callback=lambda result, error_type: observed.append((result, error_type)),
            )
        self.assertEqual(
            completion.call_count,
            1,
            f"{question!r} 必须交给模型分类；调用次数为 0 说明又出现了模型之前的短路层",
        )
        return candidate, observed

    def test_every_compound_question_reaches_the_model(self) -> None:
        """六条都必须走到模型调用，不允许被任何模型之前的层截走。

        #132 删除了整个预选层，所以这当前是结构性成立的。这条测试是护栏：
        若将来重新引入任何字面判据并吞掉这些复合句，这里会先红。
        """

        for case in self.cases:
            with self.subTest(case=case["id"]):
                # 响应内容与本条断言无关，用一个合法的最简包。
                self._classify(case["question"], '{"package_id":"direct","explicit_tool_names":[]}')

    def test_recorded_responses_replay_to_the_recorded_outcome(self) -> None:
        """回放 2026-09-20 记录的原始响应，结果应与当时观测到的一致。

        五条天气句的记录响应是单工具 `mixed_itinerary`，不满足 2–3 个工具的契约，
        因此解析拒绝、记 `invalid_response`、fail-closed 到 `clarification_only`。
        川菜馆那条记录响应本身就是 `clarification_only`，解析通过但不是业务期望。

        这条测试锁的是契约行为，不是「这六条应该失败」。模型改答对之后，
        回放旧响应仍然应当得到旧结果——那是同一份契约对同一份输入的判定。
        """

        for case in self.cases:
            with self.subTest(case=case["id"]):
                candidate, observed = self._classify(case["question"], case["recorded_response_content"])

                self.assertEqual(candidate.package_id, case["observed_package"])
                self.assertEqual(observed, [(case["observed_layer"], case["observed_error_type"])])

    def test_expected_business_packages_are_reachable_through_the_contract(self) -> None:
        """模型若按业务期望作答，当前契约必须放行。

        这条是为了把责任划清：六条失败的原因在模型选包，不在解析契约。
        谁要修 #107，不能以「契约不让过」为理由去放宽 `mixed_itinerary` 的工具数约束。
        """

        for case in self.cases:
            with self.subTest(case=case["id"]):
                expected_packages = case["expected_business_packages"]
                self.assertEqual(len(expected_packages), 1, "每条只记录一个业务期望包")
                package_id = expected_packages[0]
                tools = _EXPECTED_PACKAGE_TOOLS[package_id]

                candidate, observed = self._classify(
                    case["question"],
                    json.dumps({"package_id": package_id, "explicit_tool_names": tools}),
                )

                self.assertEqual(candidate.package_id, package_id)
                self.assertEqual(list(candidate.explicit_tool_names), tools)
                self.assertEqual(observed, [("model", None)])


if __name__ == "__main__":
    unittest.main()
