"""hold 的无缓存数据库读取；调用方必须先取得 Prompt 激活事务锁。"""

import re

from sqlalchemy.orm import Session

from app.db.models import PromptBundleHoldState


def load_prompt_bundle_hold(session: Session, project_slug: str, catalog: str) -> PromptBundleHoldState | None:
    row = session.query(PromptBundleHoldState).filter_by(project_slug=project_slug, catalog=catalog).first()
    if row is None:
        return None
    if row.state not in {"following", "held"} or type(row.generation) is not int or row.generation < 0:
        raise ValueError("Prompt hold 持久状态损坏，拒绝激活")
    if row.state == "held":
        if not isinstance(row.target_revision, str) or re.fullmatch(r"[0-9a-f]{64}", row.target_revision) is None:
            raise ValueError("Prompt hold 目标身份损坏，拒绝激活")
    elif row.target_revision is not None:
        raise ValueError("Prompt following 状态包含冲突目标，拒绝激活")
    return row
