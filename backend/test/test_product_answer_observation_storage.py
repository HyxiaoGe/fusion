"""验证跨会话留存、时间边界、隐私投影和不同分母。"""

import asyncio
import importlib.util
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.models import ProductAnswerObservation
from app.db.product_answer_observation_repository import persist_product_answer_observation
from app.services.product_answer_observation_service import aggregate_product_answer_observations
from app.services.stream import product_answer_observability as observable
from app.services.stream.product_answer_observability import (
    build_product_answer_observation,
    retain_product_answer_observation,
)


def observation(**overrides):
    fields = dict(
        reason_code="ok",
        repair_enabled=False,
        repair_available=False,
        repair_reason_code=None,
        product_result_types=["weather_results"],
        product_tool_attempted=True,
    )
    fields.update(overrides)
    return build_product_answer_observation(**fields)


class ProductAnswerStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.directory.name) / 'observations.db'}")
        ProductAnswerObservation.__table__.create(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.start = datetime(2026, 9, 20, tzinfo=timezone.utc)

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def test_records_survive_new_sessions_with_half_open_time_range_and_distinct_denominators(self):
        rows = [
            (self.start - timedelta(seconds=1), observation()),
            (self.start, observation(reason_code="unsupported_claim", repair_available=True)),
            (self.start + timedelta(seconds=1), observation()),
            (self.start + timedelta(seconds=2), observation(observation_path="weather_activity")),
            (self.start + timedelta(seconds=3), observation(observation_path="mixed_travel")),
            (self.start + timedelta(seconds=4), observation(observation_path="single_travel_comparison")),
            (self.start + timedelta(days=1), observation()),
        ]
        with patch("app.db.product_answer_observation_repository.SessionLocal", self.session_factory):
            for timestamp, payload in rows:
                persist_product_answer_observation(payload, timestamp)
        with self.session_factory() as db:
            # 无时区输入按上海时间；08:00 恰对应以上 UTC 00:00。
            result = aggregate_product_answer_observations(db, datetime(2026, 9, 20, 8), datetime(2026, 9, 21, 8))
        self.assertEqual(result["all_observed_decisions"], 5)
        self.assertEqual(result["validated_decisions"], 2)
        self.assertEqual(result["not_validated_decisions"], 3)
        self.assertEqual(result["invalid_among_validated"]["ratio"], 0.5)
        self.assertEqual(result["invalid_among_all_observed"]["ratio"], 0.2)
        self.assertEqual(result["repair_available_among_validated"]["ratio"], 0.5)
        self.assertEqual(result["reason_code"], {"ok": 1, "unsupported_claim": 1})
        self.assertEqual(result["by_category"]["risk_term"]["repair_available"]["ratio"], 1)
        self.assertEqual(result["retained_range"]["first"], self.start.isoformat())
        self.assertIsNone(result["coverage"]["complete"])
        self.assertFalse(ProductAnswerObservation.__table__.foreign_keys)

    def test_migration_creates_empty_independent_storage(self):
        migration_path = Path("alembic/versions/b2c7d9e4f610_add_product_answer_observations.py")
        spec = importlib.util.spec_from_file_location("product_observation_migration", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration_engine = create_engine("sqlite://")
        try:
            with migration_engine.begin() as connection:
                with patch.object(migration, "op", Operations(MigrationContext.configure(connection))):
                    migration.upgrade()
                self.assertFalse(inspect(connection).get_foreign_keys("product_answer_observations"))
                self.assertIn(
                    "ix_product_answer_observations_observed_at",
                    {row["name"] for row in inspect(connection).get_indexes("product_answer_observations")},
                )
            with sessionmaker(bind=migration_engine)() as db:
                self.assertEqual(db.query(ProductAnswerObservation).count(), 0)
        finally:
            migration_engine.dispose()

    def test_empty_observation_period_does_not_become_zero_percent(self):
        with self.session_factory() as db:
            result = aggregate_product_answer_observations(db, self.start, self.start + timedelta(days=1))
        self.assertEqual(result["all_observed_decisions"], 0)
        self.assertIsNone(result["invalid_among_validated"]["ratio"])
        self.assertIsNone(result["retained_range"]["first"])

    def test_builder_does_not_persist_unknown_free_text(self):
        secret = "用户或工具正文 token=private"
        payload = observation(reason_code=secret, repair_reason_code=secret, product_result_types=[secret, "thinking"])
        self.assertNotIn(secret, json.dumps(payload, ensure_ascii=False))
        self.assertEqual(payload["reason_code"], "other")
        self.assertEqual(payload["repair_reason_code"], "other")
        self.assertEqual(payload["product_result_types"], ["other", "thinking"])


class ProductAnswerStorageAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_concurrent_observations_all_commit_after_bounded_caller_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'concurrent-observations.db'}")
            ProductAnswerObservation.__table__.create(engine)
            factory = sessionmaker(bind=engine)
            executor = ThreadPoolExecutor(max_workers=2)
            first_two_started = asyncio.Event()
            release = threading.Event()
            lock = threading.Lock()
            loop = asyncio.get_running_loop()
            active = maximum_active = 0

            def write(payload, timestamp):
                nonlocal active, maximum_active
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                    if active == 2:
                        loop.call_soon_threadsafe(first_two_started.set)
                try:
                    release.wait()
                    persist_product_answer_observation(payload, timestamp)
                finally:
                    with lock:
                        active -= 1

            try:
                with (
                    patch.object(observable, "_STORE_EXECUTOR", executor),
                    patch.object(observable, "_STORE_WAIT_SECONDS", 0.02),
                    patch.object(observable, "persist_product_answer_observation", write),
                    patch("app.db.product_answer_observation_repository.SessionLocal", factory),
                ):
                    tasks = [asyncio.create_task(retain_product_answer_observation(observation())) for _ in range(2)]
                    try:
                        await asyncio.wait_for(first_two_started.wait(), 1)
                        tasks.append(asyncio.create_task(retain_product_answer_observation(observation())))
                        self.assertEqual(await asyncio.gather(*tasks), [False, False, False])
                    finally:
                        release.set()
                        await asyncio.to_thread(executor.shutdown, wait=True)
                with factory() as db:
                    self.assertEqual(db.query(ProductAnswerObservation).count(), 3)
                self.assertEqual(maximum_active, 2)
            finally:
                executor.shutdown(wait=True)
                engine.dispose()

    async def test_database_work_does_not_block_event_loop_and_is_awaited(self):
        started = threading.Event()
        released = threading.Event()

        def write(_payload, _timestamp):
            started.set()
            released.wait()

        with patch("app.services.stream.product_answer_observability.persist_product_answer_observation", write):
            task = asyncio.create_task(retain_product_answer_observation(observation()))
            try:
                for _ in range(100):
                    if started.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(started.is_set())
                self.assertFalse(task.done())
            finally:
                released.set()
                self.assertTrue(await task)

    async def test_store_failure_is_reported_without_leaking_exception_body(self):
        with (
            patch(
                "app.services.stream.product_answer_observability.persist_product_answer_observation",
                side_effect=RuntimeError("秘密数据库参数"),
            ),
            patch("app.services.stream.product_answer_observability.logger.error") as error,
        ):
            self.assertFalse(await retain_product_answer_observation(observation()))
        self.assertIn("PRODUCT_ANSWER_OBSERVATION_STORE_FAILED", error.call_args.args[0])
        self.assertNotIn("秘密数据库参数", str(error.call_args))
