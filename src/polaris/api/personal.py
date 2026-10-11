"""你自己用的接口（M6，给 M7 的网页用）：对话、批准、记忆、简报、推送反馈。全部要 OWNER_TOKEN；没配就关闭。

对话和记忆是个人数据，所以和公开的追踪、岗位接口分开，单独一个口令。
"""
import hmac
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from polaris.config import get_settings
from polaris.db.models import Notification
from polaris.db.session import session_scope


def require_owner(authorization: Annotated[str | None, Header()] = None) -> None:
    token = get_settings().owner_token
    if token is None:
        raise HTTPException(503, "personal endpoints are disabled: OWNER_TOKEN is not configured")
    if not authorization or not hmac.compare_digest(authorization.encode(), f"Bearer {token.get_secret_value()}".encode()):
        raise HTTPException(401, "invalid or missing bearer token")


router = APIRouter(prefix="/api/me", tags=["personal"], dependencies=[Depends(require_owner)])


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    thread_id: str = Field("main", max_length=64)


class ResumeIn(BaseModel):
    approve: bool
    reason: str = Field("", max_length=500)


def _turn(t: Any) -> dict[str, Any]:
    return {"thread_id": t.thread_id, "answer": t.answer, "memory_events": t.memory_events,
            "memory_questions": t.memory_questions, "approvals": t.approvals,
            "tool_calls": [{"tool": c["tool"], "args": c["args"], "ok": c["ok"]} for c in t.tool_calls],
            "latency_ms": t.latency_ms, "error": t.error}


@router.post("/chat", summary="Say something to Polaris (memory first, then the agent answers)")
def chat_endpoint(body: ChatIn) -> dict[str, Any]:
    from polaris.agent.main import chat

    return _turn(chat(body.message, body.thread_id))


@router.post("/chat/{thread_id}/resume", summary="Approve or reject the action Polaris is waiting on")
def resume_endpoint(thread_id: str, body: ResumeIn) -> dict[str, Any]:
    from polaris.agent.main import resume

    return _turn(resume(thread_id, body.approve, body.reason))


@router.get("/memory", summary="What Polaris remembers about you (on a given day), plus what it is waiting to confirm")
def memory_card_endpoint(on: date | None = None) -> dict[str, Any]:
    from polaris.tools.memory import MemoryCardArgs, memory_card

    return memory_card(MemoryCardArgs(on=on))


@router.get("/memory/{attribute}/history", summary="Every version of one memory attribute, with the sentence it came from")
def memory_history_endpoint(attribute: str, qualifier: str | None = None) -> dict[str, Any]:
    from polaris.tools.memory import MemoryHistoryArgs, memory_history

    try:
        args = MemoryHistoryArgs(attribute=attribute, qualifier=qualifier)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return memory_history(args)


@router.delete("/memory/{attribute}", summary="Forget an attribute (or one value of it); you clicked it, so no extra approval")
def memory_forget_endpoint(attribute: str, qualifier: str | None = None, value: str | None = None) -> dict[str, Any]:
    from polaris.memory.schema import ATTRIBUTES
    from polaris.memory.update import forget

    if attribute not in ATTRIBUTES:
        raise HTTPException(404, f"unknown attribute {attribute}")
    with session_scope() as s:
        return {"forgotten": len(forget(s, attribute, qualifier, value, source_text="用户在网页上点了忘掉"))}


@router.get("/notifications", summary="Recent proactive decisions (push / brief / drop) with reasons")
def notifications_endpoint(limit: int = 50, channel: Literal["push", "brief", "drop"] | None = None) -> list[dict[str, Any]]:
    with session_scope() as s:
        stmt = select(Notification).order_by(Notification.created_at.desc(), Notification.id.desc()).limit(min(limit, 200))
        if channel:
            stmt = stmt.where(Notification.channel == channel)
        return [{"id": n.id, "cite": n.cite, "title": n.title, "company": n.company, "channel": n.channel,
                 "score": float(n.score), "reasons": n.reasons, "feedback": n.feedback,
                 "created_at": n.created_at.isoformat()} for n in s.execute(stmt).scalars()]


class FeedbackIn(BaseModel):
    feedback: Literal["useful", "dismissed"]


@router.post("/notifications/{notification_id}/feedback", summary="Tell Polaris whether a push was useful")
def feedback_endpoint(notification_id: int, body: FeedbackIn) -> dict[str, Any]:
    from datetime import UTC, datetime

    with session_scope() as s:
        n = s.get(Notification, notification_id)
        if n is None:
            raise HTTPException(404, "notification not found")
        n.feedback, n.feedback_at = body.feedback, datetime.now(UTC)
    return {"ok": True}


@router.get("/brief", summary="Today's brief (markdown)")
def brief_endpoint(day: date | None = None) -> dict[str, Any]:
    from polaris.proactive.daily import BRIEFS

    day = day or date.today()
    path = BRIEFS / f"{day.isoformat()}.md"
    return {"day": day.isoformat(), "markdown": path.read_text(encoding="utf-8") if path.exists() else None}
