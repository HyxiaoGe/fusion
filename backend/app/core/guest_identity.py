"""游客身份：Fusion 自己签发的匿名令牌。

未登录用户先领取一个游客身份（服务端创建 is_guest 用户），之后像登录用户一样以
Bearer 令牌访问，会话照常归属这个用户，刷新或重开页面都还在。游客令牌用独立密钥
HS256 签名，签发方固定为 GUEST_ISSUER，与统一认证服务的 RS256 令牌互不混用。
"""

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Request

from app.core.config import settings
from app.core.revocation import get_redis

logger = logging.getLogger(__name__)

GUEST_ISSUER = "fusion-guest"
GUEST_TOKEN_TYPE = "guest"
_GUEST_ALGORITHM = "HS256"
_NEW_GUEST_KEY_PREFIX = "guest_new"
_CHINA_TZ = timezone(timedelta(hours=8))


class GuestTokenError(Exception):
    pass


def guest_mode_available() -> bool:
    return settings.GUEST_MODE_ENABLED and bool(settings.GUEST_TOKEN_SECRET)


def issue_guest_token(user_id: str, *, now: float | None = None) -> tuple[str, int]:
    issued_at = int(now if now is not None else time.time())
    expires_at = issued_at + settings.GUEST_TOKEN_TTL_DAYS * 24 * 3600
    token = jwt.encode(
        {
            "iss": GUEST_ISSUER,
            "sub": user_id,
            "typ": GUEST_TOKEN_TYPE,
            "iat": issued_at,
            "exp": expires_at,
        },
        settings.GUEST_TOKEN_SECRET,
        algorithm=_GUEST_ALGORITHM,
    )
    return token, expires_at


def is_guest_token(token: str) -> bool:
    """只看未验签的签发方来分流；真正的信任来自 verify_guest_token 的验签。"""
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return False
    return claims.get("iss") == GUEST_ISSUER


def verify_guest_token(token: str) -> str:
    if not guest_mode_available():
        raise GuestTokenError("guest mode disabled")
    try:
        claims = jwt.decode(
            token,
            settings.GUEST_TOKEN_SECRET,
            algorithms=[_GUEST_ALGORITHM],
            issuer=GUEST_ISSUER,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise GuestTokenError(str(exc)) from exc
    subject = claims.get("sub")
    if claims.get("typ") != GUEST_TOKEN_TYPE or not isinstance(subject, str) or not subject:
        raise GuestTokenError("invalid guest claims")
    return subject


def client_ip(request: Request) -> tuple[str, str]:
    """返回 (IP, 来源)。外层经 Cloudflare 与前端代理转发，优先取 Cloudflare 给出的真实地址。"""
    for header in ("cf-connecting-ip", "x-real-ip"):
        value = (request.headers.get(header) or "").strip()
        if value:
            return value, header
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded, "x-forwarded-for"
    host = request.client.host if request.client else ""
    return host or "unknown", "peer"


def ip_digest(ip: str) -> str:
    """限流键与日志只记 IP 摘要，不落原始地址。"""
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def china_day(now: float | None = None) -> str:
    moment = datetime.fromtimestamp(now if now is not None else time.time(), tz=_CHINA_TZ)
    return moment.strftime("%Y%m%d")


def reserve_new_guest_slot(ip: str, *, now: float | None = None) -> bool:
    """同一 IP 每天（北京时间）新建游客身份的次数上限。Redis 不可用时拒绝新建，登录用户不受影响。"""
    key = f"{_NEW_GUEST_KEY_PREFIX}:{china_day(now)}:{ip_digest(ip)}"
    client = get_redis()
    count = client.incr(key)
    if count == 1:
        client.expire(key, 2 * 24 * 3600)
    return count <= settings.GUEST_NEW_IDENTITIES_PER_IP_PER_DAY
