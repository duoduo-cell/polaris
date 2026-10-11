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


class ToolIn(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


@router.post("/tools/{name}", summary="Run one read-only tool directly (the same tools the agent uses; traced the same way)")
def tool_endpoint(name: str, body: ToolIn) -> dict[str, Any]:
    import json

    from polaris.tools import REGISTRY
    from polaris.tools.runtime import ToolRuntime

    spec = REGISTRY.get(name)
    if spec is None or not spec.read_only:
        raise HTTPException(404, f"no read-only tool named {name}")
    runtime = ToolRuntime(agent="web")
    out = runtime.invoke(spec, body.args)
    call = runtime.calls[-1]
    try:
        result: Any = json.loads(out)
    except ValueError:
        result = out
    return {"ok": call["ok"], "error_kind": call["error_kind"], "result": result}


REPORTS = ("M1", "M2", "M3", "M4", "M5", "proactive", "injection", "market")


@router.get("/reports", summary="Evaluation reports that can be shown")
def reports_endpoint() -> list[dict[str, Any]]:
    from polaris.config import ROOT

    out = []
    for name in REPORTS:
        path = ROOT / "eval" / "reports" / f"{name}.md"
        if path.exists():
            text_ = path.read_text(encoding="utf-8")
            title = next((line.lstrip("# ").strip() for line in text_.splitlines() if line.startswith("# ")), name)
            out.append({"name": name, "title": title})
    return out


@router.get("/reports/{name}", summary="One evaluation report (markdown)")
def report_endpoint(name: str) -> dict[str, Any]:
    from polaris.config import ROOT

    if name not in REPORTS:
        raise HTTPException(404, "unknown report")
    path = ROOT / "eval" / "reports" / f"{name}.md"
    if not path.exists():
        raise HTTPException(404, "report not generated yet")
    return {"name": name, "markdown": path.read_text(encoding="utf-8")}


@router.get("/chat/{thread_id}/messages", summary="The conversation so far (from the LangGraph checkpoint)")
def messages_endpoint(thread_id: str) -> dict[str, Any]:
    from polaris.agent.main import _interrupts, checkpointer
    from polaris.agent.minimal import text_of

    saved = checkpointer().get_tuple({"configurable": {"thread_id": thread_id}})
    if saved is None:
        return {"thread_id": thread_id, "messages": [], "approvals": []}
    out = []
    for m in saved.checkpoint["channel_values"].get("messages", []):
        role = {"human": "user", "ai": "assistant"}.get(m.type)
        content = text_of(m.content)
        if role and content:
            out.append({"role": role, "content": content})
    # 等你批准的动作在检查点的 pending_writes 里：(任务编号, "__interrupt__", [Interrupt, ...])
    waiting = [i for _, channel, value in saved.pending_writes or [] if channel == "__interrupt__" for i in value]
    return {"thread_id": thread_id, "messages": out, "approvals": _interrupts({"__interrupt__": waiting})}
