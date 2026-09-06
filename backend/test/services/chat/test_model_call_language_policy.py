import unittest
from copy import deepcopy

from app.ai.prompts.agent_loop import VISIBLE_RESPONSE_LANGUAGE_PROMPT
from app.ai.prompts.prompt_message import PromptMessage
from app.ai.prompts.section_ids import DEEP_RESEARCH_STAGE, VISIBLE_RESPONSE_LANGUAGE
from app.services.chat.model_call_language_policy import finalize_model_call_language_policy


class ModelCallLanguagePolicyTests(unittest.TestCase):
    def test_moves_single_contract_to_last_effective_system_instruction(self):
        messages = [
            PromptMessage(
                role="system",
                content="旧语言规则",
                section_id=VISIBLE_RESPONSE_LANGUAGE,
            ),
            PromptMessage(
                role="system",
                content="深度研究阶段规则",
                section_id=DEEP_RESEARCH_STAGE,
            ),
            PromptMessage(role="user", content="具身智能产业的影响"),
        ]
        original = list(messages)

        finalized = finalize_model_call_language_policy(messages)

        self.assertEqual(messages, original)
        self.assertEqual(_contract_count(finalized), 1)
        self.assertEqual(finalized[-2]["role"], "system")
        self.assertEqual(finalized[-2].section_id, VISIBLE_RESPONSE_LANGUAGE)
        self.assertEqual(finalized[-2].content, VISIBLE_RESPONSE_LANGUAGE_PROMPT)
        self.assertEqual(finalized[0].section_id, DEEP_RESEARCH_STAGE)

    def test_preserves_tool_transaction_and_raw_reasoning_when_repair_rule_is_appended(self):
        messages = [
            {"role": "system", "content": "身份规则"},
            {"role": "user", "content": "分析最新进展"},
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "I should inspect the source.",
                "tool_calls": [{"id": "call-1", "function": {"name": "web_search"}}],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "搜索结果"},
            {"role": "system", "content": "修复计划状态后继续"},
        ]
        original = deepcopy(messages)

        finalized = finalize_model_call_language_policy(messages)

        self.assertEqual(messages, original)
        without_language = [item for item in finalized if item.section_id != VISIBLE_RESPONSE_LANGUAGE]
        self.assertEqual(without_language, original)
        self.assertEqual(finalized[2], original[2])
        self.assertEqual(finalized[3], original[3])
        self.assertEqual(finalized[-1].section_id, VISIBLE_RESPONSE_LANGUAGE)
        self.assertEqual(_contract_count(finalized), 1)

    def test_preserves_normal_tool_transaction_without_a_tail_repair_system(self):
        messages = [
            {"role": "system", "content": "身份规则"},
            {"role": "user", "content": "分析最新进展"},
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "I should inspect the source.",
                "tool_calls": [{"id": "call-1", "function": {"name": "web_search"}}],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "搜索结果"},
        ]
        original = deepcopy(messages)

        finalized = finalize_model_call_language_policy(messages)

        self.assertEqual(messages, original)
        without_language = [item for item in finalized if item.section_id != VISIBLE_RESPONSE_LANGUAGE]
        self.assertEqual(without_language, original)
        self.assertEqual(finalized[1].section_id, VISIBLE_RESPONSE_LANGUAGE)
        self.assertEqual(_contract_count(finalized), 1)

    def test_is_idempotent_and_adds_a_system_instruction_when_missing(self):
        messages = [{"role": "user", "content": "Explain optimistic locking."}]

        once = finalize_model_call_language_policy(messages)
        twice = finalize_model_call_language_policy(once)

        self.assertEqual(once, twice)
        self.assertEqual(once[0], {"role": "system", "content": VISIBLE_RESPONSE_LANGUAGE_PROMPT})
        self.assertEqual(_contract_count(once), 1)

    def test_contract_follows_real_user_language_instead_of_forcing_chinese(self):
        self.assertIn("last actual user request", VISIBLE_RESPONSE_LANGUAGE_PROMPT)
        self.assertIn("internal control messages must not change", VISIBLE_RESPONSE_LANGUAGE_PROMPT)
        self.assertIn("reasoning_content you emit is streamed to the user verbatim", VISIBLE_RESPONSE_LANGUAGE_PROMPT)
        self.assertIn("first reasoning_content token", VISIBLE_RESPONSE_LANGUAGE_PROMPT)
        self.assertNotIn("must use Chinese", VISIBLE_RESPONSE_LANGUAGE_PROMPT)

    def test_identity_replaces_stale_language_contract_without_scanning_other_bodies(self):
        quoted_rule = PromptMessage(
            role="system",
            content=f"用户要求引用以下文本，不应删除：{VISIBLE_RESPONSE_LANGUAGE_PROMPT}",
            section_id="user_preferences",
        )
        stale = PromptMessage(
            role="system",
            content="已热更新过的旧语言规则正文",
            section_id="visible_response_language",
        )

        finalized = finalize_model_call_language_policy(
            [quoted_rule, stale, PromptMessage(role="user", content="继续")]
        )

        self.assertIn(quoted_rule, finalized)
        self.assertEqual(
            [message.section_id for message in finalized].count("visible_response_language"),
            1,
        )
        current = next(message for message in finalized if message.section_id == "visible_response_language")
        self.assertEqual(current.content, VISIBLE_RESPONSE_LANGUAGE_PROMPT)


def _contract_count(messages: list[PromptMessage]) -> int:
    return sum(str(message.get("content") or "").count(VISIBLE_RESPONSE_LANGUAGE_PROMPT) for message in messages)
