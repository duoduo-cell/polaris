"""Spending limits for the public synthetic demo (#17): model calls per Beijing day, and requests per visitor in 10 minutes."""
import time
from collections import defaultdict, deque
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from sqlalchemy import func, select

from polaris.config import get_settings
from polaris.db.models import LLMCall
from polaris.db.session import session_scope
from polaris.report import BEIJING

WINDOW_SECONDS = 600
DAILY_MESSAGE = "今天的演示额度用完了，北京时间零点后再来试试。"
CLIENT_MESSAGE = "演示里的请求有点密，过几分钟再试。"
_recent: dict[str, deque[float]] = defaultdict(deque)


def _now() -> datetime:
    return datetime.now(UTC)


def beijing_midnight(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(BEIJING).date(), datetime.min.time(), tzinfo=BEIJING)


def calls_today(now: datetime) -> int:
    """Count from the existing traces, so every model call is included, whichever path made it."""
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(LLMCall).where(LLMCall.created_at >= beijing_midnight(now))) or 0


def client_key(request: Request) -> str:
    # Caddy sets X-Forwarded-For and the web proxy passes it on; locally everyone shares one key.
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def guard(request: Request) -> None:
    """FastAPI dependency for routes that will call a model. A no-op outside demo mode."""
    settings = get_settings()
    if not settings.demo_mode:
        return
    if calls_today(_now()) >= settings.demo_daily_model_calls:
        raise HTTPException(429, DAILY_MESSAGE)
    stamps, now = _recent[client_key(request)], time.monotonic()
    while stamps and now - stamps[0] > WINDOW_SECONDS:
        stamps.popleft()
    if len(stamps) >= settings.demo_requests_per_client:
        raise HTTPException(429, CLIENT_MESSAGE)
    stamps.append(now)
