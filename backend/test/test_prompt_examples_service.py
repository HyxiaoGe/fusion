import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import PromptExample
from app.services import prompt_examples_service


class PromptExamplesServiceTests(unittest.TestCase):
    def test_default_pool_can_fill_home_starter_row(self):
        self.assertGreaterEqual(len(prompt_examples_service.DEFAULT_EXAMPLES), 9)

    def test_balanced_sample_does_not_mutate_source_pool(self):
        examples = [
            {"category": "news", "question": "新闻问题一"},
            {"category": "news", "question": "新闻问题二"},
            {"category": "tech", "question": "技术问题一"},
            {"category": "tech", "question": "技术问题二"},
            {"category": "general", "question": "通用问题一"},
            {"category": "general", "question": "通用问题二"},
        ]
        original = [dict(item) for item in examples]

        sampled = prompt_examples_service._balanced_sample(examples, 3)

        self.assertEqual(len(sampled), 3)
        self.assertEqual(examples, original)
        self.assertEqual({item["category"] for item in sampled}, {"news", "tech", "general"})


class StorePromptExamplesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        PromptExample.__table__.create(engine)
        # 与生产 SessionLocal 一致
        self.db = sessionmaker(autoflush=False, bind=engine)()

    def tearDown(self):
        self.db.close()

    async def test_probe_questions_are_merged_deduplicated_and_cached(self):
        self.db.add(PromptExample(question="已有问题", category="news", source="kimi", is_active=True))
        self.db.commit()
        cache = AsyncMock()

        with patch.object(prompt_examples_service, "_cache_to_redis", cache):
            added = await prompt_examples_service.store_prompt_examples(
                [
                    {"question": "已有问题", "category": "news"},
                    {"question": "明天杭州会下雨吗", "category": "weather"},
                    {"question": "明天杭州会下雨吗", "category": "weather"},
                    {"question": "  ", "category": "tech"},
                ],
                source="probe",
                db=self.db,
            )

        self.assertEqual(added, 1)
        row = self.db.query(PromptExample).filter(PromptExample.question == "明天杭州会下雨吗").one()
        self.assertEqual((row.category, row.source), ("weather", "probe"))
        cached = cache.await_args.args[0]
        self.assertEqual({item["question"] for item in cached}, {"已有问题", "明天杭州会下雨吗"})

    async def test_pool_keeps_at_most_200_active_questions(self):
        for index in range(200):
            self.db.add(PromptExample(question=f"旧问题{index}", category="news", is_active=True))
        self.db.commit()

        with patch.object(prompt_examples_service, "_cache_to_redis", AsyncMock()):
            await prompt_examples_service.store_prompt_examples(
                [{"question": "新问题", "category": "tech"}], source="probe", db=self.db
            )

        self.assertEqual(self.db.query(PromptExample).filter(PromptExample.is_active == True).count(), 200)  # noqa: E712


if __name__ == "__main__":
    unittest.main()
