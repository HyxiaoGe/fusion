"""产品回答观测的独立事务写入与数据库端聚合。"""

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.models import ProductAnswerObservation
from app.utils.time import utc_now


def persist_product_answer_observation(payload: dict, observed_at: datetime | None = None) -> None:
    with SessionLocal() as db:
        db.add(ProductAnswerObservation(observed_at=observed_at or utc_now(), **payload))
        db.commit()


def query_product_answer_observations(db: Session, start: datetime, end: datetime) -> tuple[list, tuple]:
    """固定维度分组，无需把整段观测明细载入内存。时间范围左闭右开。"""
    model = ProductAnswerObservation
    columns = (
        model.observation_path,
        model.validated,
        model.reason_code,
        model.reason_category,
        model.is_valid,
        model.repair_available,
        model.product_tool_attempted,
    )
    predicates = (model.observed_at >= start, model.observed_at < end)
    groups = db.query(*columns, func.count(model.id)).filter(*predicates).group_by(*columns).all()
    boundaries = db.query(func.min(model.observed_at), func.max(model.observed_at)).filter(*predicates).one()
    return groups, boundaries
