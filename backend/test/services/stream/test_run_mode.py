import unittest

from app.ai.prompts.agent_loop import RESEARCH_EVIDENCE_SUMMARY_PROMPT
from app.ai.prompts.section_ids import DEEP_RESEARCH_STAGE, RESEARCH_EVIDENCE_WORKSET
from app.services.stream.agent_loop_state import AgentLoopState
from app.services.stream.deep_research_mode import DEEP_RESEARCH_RUN_MODE
from app.services.stream.limit_summary import append_limit_summary_prompt
from app.services.stream.run_mode import STANDARD_RUN_MODE, resolve_run_mode
from test.services.stream.test_research_evidence import _read_block, _search_block


def _state_with_candidate_and_read() -> AgentLoopState:
    state = AgentLoopState()
    state.record_research_content_blocks(
        [_search_block(), _read_block("https://example.com/a", evidence_id="ev-a", citation_index=2)],
        summaries={"ev-web-report": ("官方摘要", ["关键发现一"]), "ev-a": ("原文摘要", ["关键发现二"])},
    )
    return state


def _workset_prompt(messages) -> str:
    return next(message.content for message in messages if message.section_id == RESEARCH_EVIDENCE_WORKSET)


class ResolveRunModeTests(unittest.TestCase):
    def test_task_mode_selects_run_mode(self):
        self.assertIs(resolve_run_mode("deep_research"), DEEP_RESEARCH_RUN_MODE)
        self.assertIs(resolve_run_mode("standard"), STANDARD_RUN_MODE)


class SummaryMessagesTests(unittest.TestCase):
    def test_deep_research_summary_only_keeps_read_sources(self):
        """深研成文只能引用读到正文的来源，未读的搜索候选不进收尾上下文。"""

        messages = DEEP_RESEARCH_RUN_MODE.summary_messages(
            [{"role": "user", "content": "研究一下"}],
            state=_state_with_candidate_and_read(),
        )

        prompt = _workset_prompt(messages)
        self.assertIn("evidence_id=ev-a status=read_success", prompt)
        self.assertNotIn("status=candidate", prompt)
        self.assertNotIn(DEEP_RESEARCH_STAGE, [message.section_id for message in messages])

    def test_standard_summary_lists_candidates(self):
        messages = STANDARD_RUN_MODE.summary_messages(
            [{"role": "user", "content": "查一下"}],
            state=_state_with_candidate_and_read(),
        )

        prompt = _workset_prompt(messages)
        self.assertIn("evidence_id=ev-web-report status=candidate", prompt)
        self.assertIn("evidence_id=ev-a status=read_success", prompt)


class SummaryPromptTests(unittest.TestCase):
    def _summary_prompt(self, mode, summary_finish_reason="limit_summary") -> str:
        messages = [{"role": "user", "content": "研究一下"}]
        append_limit_summary_prompt(messages, summary_finish_reason=summary_finish_reason, mode=mode)
        return messages[-1].content

    def test_deep_research_summary_appends_evidence_requirements(self):
        self.assertIn(RESEARCH_EVIDENCE_SUMMARY_PROMPT, self._summary_prompt(DEEP_RESEARCH_RUN_MODE))
        self.assertNotIn(RESEARCH_EVIDENCE_SUMMARY_PROMPT, self._summary_prompt(STANDARD_RUN_MODE))

    def test_evidence_summary_prompt_is_not_repeated(self):
        prompt = self._summary_prompt(DEEP_RESEARCH_RUN_MODE, "research_evidence_repair_exhausted")

        self.assertEqual(prompt.count(RESEARCH_EVIDENCE_SUMMARY_PROMPT), 1)


if __name__ == "__main__":
    unittest.main()
