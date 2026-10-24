import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import PromptExample
from app.services import prompt_examples_service


class PromptExamplesServiceTests(unittest.TestCase):
    def test_default_pool_can_fill_home_starter_row(self):
        self.assertGreaterEqual(len(prompt_examples_service.DEFAULT_EXAMPLES), 9)

    def test_default_pool_only_uses_catalog_categories_without_requirements(self):
        for item in prompt_examples_service.DEFAULT_EXAMPLES:
            capability = prompt_examples_service.CAPABILITY_BY_KEY[item["category"]]
            self.assertIsNone(capability.requires, item)

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


class VisibleExamplesTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_unexpired_catalog_questions_are_returned_with_requires(self):
        now = datetime(2026, 10, 12, 12, 0)
        cached = {
            "examples": [
                {"question": "画一只戴墨镜的柴犬", "category": "image", "expires_at": "2026-10-19T05:00:00"},
                {"question": "今天杭州会下雨吗", "category": "weather", "expires_at": "2026-10-12T11:00:00"},
                {"question": "10月2号上海到杭州高铁还有票吗", "category": "travel", "expires_at": None},
                {"question": "帮我写一份青岛三日游攻略", "category": "document", "expires_at": None},
            ],
            "refreshed_at": "2026-10-12T05:00:00",
        }
        with (
            patch.object(prompt_examples_service, "_read_from_redis", AsyncMock(return_value=cached)),
            patch.object(prompt_examples_service, "_china_now", return_value=now),
        ):
            data = await prompt_examples_service.get_prompt_examples(10)

        self.assertEqual(
            sorted(data["examples"], key=lambda item: item["category"]),
            [
                {"question": "帮我写一份青岛三日游攻略", "category": "document", "requires": None},
                {"question": "画一只戴墨镜的柴犬", "category": "image", "requires": "image_generation"},
            ],
        )

    async def test_falls_back_to_defaults_when_nothing_visible(self):
        cached = {"examples": [{"question": "旧探针题", "category": "travel"}], "refreshed_at": None}
        with patch.object(prompt_examples_service, "_read_from_redis", AsyncMock(return_value=cached)):
            data = await prompt_examples_service.get_prompt_examples(5)

        self.assertEqual(len(data["examples"]), 5)
        self.assertTrue(all(item["requires"] is None for item in data["examples"]))


class StoreExpiryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        PromptExample.__table__.create(engine)
        self.db = sessionmaker(autoflush=False, bind=engine)()

    def tearDown(self):
        self.db.close()

    async def test_expired_questions_are_deactivated_on_store(self):
        now = datetime(2026, 10, 12, 5, 0)
        self.db.add(
            PromptExample(
                question="昨天的天气题", category="weather", is_active=True, expires_at=now - timedelta(hours=1)
            )
        )
        self.db.commit()

        with (
            patch.object(prompt_examples_service, "_cache_to_redis", AsyncMock()),
            patch.object(prompt_examples_service, "_china_now", return_value=now),
        ):
            added = await prompt_examples_service.store_prompt_examples(
                [{"question": "今天的天气题", "category": "weather", "expires_at": now + timedelta(hours=30)}],
                source="catalog",
                db=self.db,
            )

        self.assertEqual(added, 1)
        active = {row.question for row in self.db.query(PromptExample).filter(PromptExample.is_active == True)}  # noqa: E712
        self.assertEqual(active, {"今天的天气题"})


if __name__ == "__main__":
    unittest.main()
