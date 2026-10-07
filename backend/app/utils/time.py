"""统一的时间工具：存储按 UTC，交给模型和用户看的时刻按北京时间。"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def utc_now() -> datetime:
    """返回带 UTC 时区信息的当前时间。"""
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """把数据库时间统一为 UTC aware；历史无时区值按其既有 UTC 语义解释。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def as_china_time(value: datetime) -> datetime:
    """把带时区的时刻换成北京时间表示；模型会把 UTC 时刻当本地时间念出来。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return value.astimezone(CHINA_TZ)
