"""
示例问题服务

负责入池、缓存、读取首页「今日灵感」（来源：inspiration_generator 每天按功能目录出题）。
- 写入 PostgreSQL（持久化），每道题带有效期，过期即不再展示
- 缓存到 Redis（TTL 2 小时）
- 读取时优先 Redis，miss 则查 PostgreSQL 并回填
- 只展示功能目录里的分类，并带上所需功能，前端按用户权益过滤
"""

import json
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.logger import app_logger as logger
from app.core.redis import get_redis_pool
from app.db.database import SessionLocal
from app.db.models import PromptExample
from app.services.inspiration_catalog import CAPABILITY_BY_KEY

REDIS_KEY = "prompt:examples"
REDIS_TTL = 7200  # 2 小时
POOL_LIMIT = 200
_CHINA_TZ = timezone(timedelta(hours=8))

# 默认 fallback 问题（池子为空时使用），只用不需要额外功能的分类
DEFAULT_EXAMPLES = [
    {"category": "code", "question": "写一个 Python 快速排序函数"},
    {"category": "code", "question": "如何用 Docker 部署 FastAPI 服务"},
    {"category": "code", "question": "解释 React useEffect 的执行时机"},
    {"category": "knowledge", "question": "解释一下量子计算的基本原理"},
    {"category": "knowledge", "question": "为什么秋天的天空特别蓝"},
    {"category": "research", "question": "对比 PostgreSQL 和 MongoDB 适用场景"},
    {"category": "research", "question": "国产大模型现在发展得怎么样"},
    {"category": "document", "question": "帮我写一份一个月的健身计划"},
    {"category": "news", "question": "最近 AI 圈有什么大新闻"},
]


def _china_now() -> datetime:
    """数据库里的时间都是不带时区的北京时间。"""
    return datetime.now(_CHINA_TZ).replace(tzinfo=None)


async def store_prompt_examples(questions: list[dict], *, source: str, db=None) -> int:
    """把一批 {question, category, expires_at?} 并入示例池，返回新增条数。

    累积写入、按题目去重；已过期的题先下架；池子上限 200 条，超出的旧题标记为不活跃；
    写完刷新 Redis 全量缓存。
    """
    own_session = db is None
    db = db or SessionLocal()
    try:
        now = _china_now()

        db.query(PromptExample).filter(
            PromptExample.is_active == True,  # noqa: E712
            PromptExample.expires_at.isnot(None),
            PromptExample.expires_at <= now,
        ).update({"is_active": False}, synchronize_session=False)

        existing = set(r[0] for r in db.query(PromptExample.question).filter(PromptExample.is_active == True).all())  # noqa: E712
        active_before = db.query(PromptExample).filter(PromptExample.is_active == True).count()  # noqa: E712

        new_count = 0
        for item in questions:
            question = str(item.get("question") or "").strip()
            category = str(item.get("category") or "general").strip() or "general"
            if not question or question in existing:
                continue
            db.add(
                PromptExample(
                    question=question,
                    category=category,
                    source=source,
                    is_active=True,
                    created_at=now,
                    expires_at=item.get("expires_at"),
                )
            )
            existing.add(question)
            new_count += 1

        # 池子上限 200 条，超出的旧数据标记为不活跃（加题前计数，不依赖 autoflush）
        active_count = active_before + new_count
        if active_count > POOL_LIMIT:
            overflow = active_count - POOL_LIMIT
            old_ids = [
                r[0]
                for r in db.query(PromptExample.id)
                .filter(PromptExample.is_active == True)  # noqa: E712
                .order_by(PromptExample.created_at.asc())
                .limit(overflow)
                .all()
            ]
            if old_ids:
                db.query(PromptExample).filter(PromptExample.id.in_(old_ids)).update(
                    {"is_active": False}, synchronize_session=False
                )

        db.commit()
        logger.info(f"示例问题已写入数据库（{source}）: {new_count} 条新增，池子共 {min(active_count, POOL_LIMIT)} 条")

        # 写 Redis 缓存（全量活跃池，不只是本批次）
        all_active = db.query(PromptExample).filter(PromptExample.is_active == True).all()  # noqa: E712
        await _cache_to_redis([_to_cached(row) for row in all_active], now)
        return new_count

    except Exception as e:
        logger.error(f"刷新示例问题失败: {e}")
        db.rollback()
        return 0
    finally:
        if own_session:
            db.close()


async def get_prompt_examples(limit: int = 8) -> dict:
    """
    读取示例问题，优先 Redis，miss 则查 PostgreSQL。
    只保留功能目录里、未过期的题，按 category 均匀采样后随机返回 limit 条，每条带所需功能。
    """
    # 1. 尝试 Redis
    cached = await _read_from_redis()
    if cached:
        examples = cached["examples"]
        refreshed_at = cached["refreshed_at"]
    else:
        # 2. 查 PostgreSQL
        examples, refreshed_at = _read_from_db()
        if examples:
            await _cache_to_redis(examples, refreshed_at)

    examples = _visible(examples or [], _china_now())

    # 3. fallback
    if not examples:
        examples = DEFAULT_EXAMPLES
        refreshed_at = None

    # 4. 按 category 均匀采样
    sampled = _balanced_sample(examples, limit)

    # refreshed_at 可能是 datetime 对象（从 DB）或字符串（从 Redis 缓存）
    if refreshed_at and hasattr(refreshed_at, "isoformat"):
        refreshed_at = refreshed_at.isoformat()

    return {
        "examples": [
            {
                "question": item["question"],
                "category": item["category"],
                "requires": CAPABILITY_BY_KEY[item["category"]].requires,
            }
            for item in sampled
        ],
        "refreshed_at": refreshed_at,
    }


def _to_cached(row: PromptExample) -> dict:
    return {
        "question": row.question,
        "category": row.category,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


def _visible(examples: list[dict], now: datetime) -> list[dict]:
    """只保留功能目录里的分类（探针旧题等不展示）和未过期的题。"""
    visible = []
    for item in examples:
        if item.get("category") not in CAPABILITY_BY_KEY:
            continue
        expires_at = item.get("expires_at")
        if expires_at and datetime.fromisoformat(expires_at) <= now:
            continue
        visible.append(item)
    return visible


def _balanced_sample(examples: list[dict], limit: int) -> list[dict]:
    """按 category 均匀采样，然后随机排序"""
    by_category: dict[str, list[dict]] = {}
    for item in examples:
        by_category.setdefault(item["category"], []).append(item)

    result = []
    categories = list(by_category.keys())
    if not categories:
        return []

    # 轮流从每个 category 取
    per_category = max(1, limit // len(categories))
    for cat in categories:
        items = by_category[cat]
        random.shuffle(items)
        result.extend(items[:per_category])

    # 补足到 limit
    remaining = [item for item in examples if item not in result]
    random.shuffle(remaining)
    result.extend(remaining[: limit - len(result)])

    random.shuffle(result)
    return result[:limit]


def _read_from_db() -> tuple[Optional[list[dict]], Optional[datetime]]:
    """从 PostgreSQL 读取活跃的示例问题"""
    db = SessionLocal()
    try:
        rows = (
            db.query(PromptExample)
            .filter(PromptExample.is_active == True)  # noqa: E712
            .order_by(PromptExample.created_at.desc())
            .all()
        )

        if not rows:
            return None, None

        examples = [_to_cached(r) for r in rows]
        refreshed_at = rows[0].created_at
        return examples, refreshed_at
    except Exception as e:
        logger.error(f"读取示例问题失败: {e}")
        return None, None
    finally:
        db.close()


async def _cache_to_redis(examples: list[dict], refreshed_at) -> None:
    """写入 Redis 缓存"""
    redis = get_redis_pool()
    if not redis:
        return
    try:
        data = {
            "examples": examples,
            "refreshed_at": refreshed_at.isoformat() if refreshed_at else None,
        }
        await redis.set(REDIS_KEY, json.dumps(data, ensure_ascii=False), ex=REDIS_TTL)
    except Exception as e:
        logger.warning(f"写入 Redis 缓存失败: {e}")


async def _read_from_redis() -> Optional[dict]:
    """从 Redis 读取缓存"""
    redis = get_redis_pool()
    if not redis:
        return None
    try:
        raw = await redis.get(REDIS_KEY)
        if raw:
            return json.loads(raw)
    except Exception as e:
        logger.warning(f"读取 Redis 缓存失败: {e}")
    return None
