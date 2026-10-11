"""你自己用的接口（M6，给 M7 的网页用）：对话、批准、记忆、简报、推送反馈。全部要 OWNER_TOKEN；没配就关闭。

对话和记忆是个人数据，所以和公开的追踪、岗位接口分开，单独一个口令。
"""
import hmac
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from polaris.config import get_settings
from polaris.db.models import Notification
from polaris.db.session import session_scope
from polaris.report import BEIJING


def require_owner(authorization: Annotated[str | None, Header()] = None) -> None:
    token = get_settings().owner_token
    if token is None:
        raise HTTPException(503, "personal endpoints are disabled: OWNER_TOKEN is not configured")
    if not authorization or not hmac.compare_digest(authorization.encode(), f"Bearer {token.get_secret_value()}".encode()):
        raise HTTPException(401, "invalid or missing bearer token")


router = APIRouter(prefix="/api/me", tags=["personal"], dependencies=[Depends(require_owner)])


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    thread_id: str = Field("main", min_length=1, max_length=64)

    @field_validator("message")
    @classmethod
    def not_blank(cls, message: str) -> str:
        if not message.strip():
            raise ValueError("消息不能为空")
        return message


class ResumeIn(BaseModel):
    approve: bool
    reason: str = Field("", max_length=500)


class TaskIn(ChatIn):
    request_id: UUID


class ResumeTaskIn(ResumeIn):
    request_id: UUID


def _submit(thread_id: str, kind: str, payload: dict[str, Any], request_id: UUID) -> dict[str, Any]:
    from polaris.agent.tasks import TaskConflict, submit

    try:
        return submit(thread_id, kind, payload, str(request_id))
    except TaskConflict as e:
        raise HTTPException(409, str(e)) from e


@router.post("/chat/tasks", status_code=202, summary="Persist a message and start an independent background task")
def submit_chat(body: TaskIn) -> dict[str, Any]:
    return _submit(body.thread_id, "chat", {"message": body.message}, body.request_id)


@router.post("/chat/{thread_id}/resume/tasks", status_code=202, summary="Persist an approval decision (idempotent)")
def submit_resume(thread_id: str, body: ResumeTaskIn) -> dict[str, Any]:
    return _submit(thread_id, "resume", {"approve": body.approve, "reason": body.reason}, body.request_id)


@router.get("/chat/threads", summary="Recent conversations, including background work and pending approvals")
def chat_threads() -> list[dict[str, Any]]:
    from polaris.agent.tasks import thread_list

    return thread_list()


@router.get("/chat/tasks/{task_id}", summary="Durable progress and result, available after reconnecting")
def task_status(task_id: str) -> dict[str, Any]:
    from polaris.agent.tasks import task_dict
    from polaris.db.models import ChatTask

    with session_scope() as s:
        task = s.get(ChatTask, task_id)
        if not task:
            raise HTTPException(404, "task not found")
        return task_dict(task)


def _task_events(task_id: str) -> Any:
    import time

    seen = 0
    while True:
        task = task_status(task_id)
        yield from task["activities"][seen:]
        seen = len(task["activities"])
        if task["state"] not in ("queued", "running"):
            if task["result"]:
                if task["result"]["answer"]:
                    yield {"type": "token", "text": task["result"]["answer"]}
                yield {"type": "done", "turn": task["result"]}
            else:
                yield {"type": "error", "message": task["error"] or "任务已中断"}
            return
        time.sleep(0.2)


def _wait_task(task_id: str) -> dict[str, Any]:
    for event in _task_events(task_id):
        if event["type"] == "done":
            return event["turn"]
        if event["type"] == "error":
            raise HTTPException(502, event["message"])
    raise HTTPException(502, "任务没有返回结果")


def _sse(events: Any) -> Any:
    import json

    from fastapi.responses import StreamingResponse

    def body():
        for e in events:
            yield f"data: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/chat", summary="Say something to Polaris (memory first, then the agent answers)")
def chat_endpoint(body: ChatIn) -> dict[str, Any]:
    task = _submit(body.thread_id, "chat", {"message": body.message}, uuid4())
    return _wait_task(task["id"])


@router.post("/chat/{thread_id}/resume", summary="Approve or reject the action Polaris is waiting on")
def resume_endpoint(thread_id: str, body: ResumeIn) -> dict[str, Any]:
    task = _submit(thread_id, "resume", {"approve": body.approve, "reason": body.reason}, uuid4())
    return _wait_task(task["id"])


@router.post("/chat/stream", summary="Same as /chat, streamed as server-sent events: memory, tool, token, done")
def chat_stream_endpoint(body: ChatIn) -> Any:
    task = _submit(body.thread_id, "chat", {"message": body.message}, uuid4())
    return _sse(_task_events(task["id"]))


@router.post("/chat/{thread_id}/resume/stream", summary="Same as /resume, streamed")
def resume_stream_endpoint(thread_id: str, body: ResumeIn) -> Any:
    task = _submit(thread_id, "resume", {"approve": body.approve, "reason": body.reason}, uuid4())
    return _sse(_task_events(task["id"]))


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

    day = day or datetime.now(BEIJING).date()
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


@router.get("/chat/{thread_id}/messages", summary="Actual transcript and recoverable tasks; never the model context")
def messages_endpoint(thread_id: str) -> dict[str, Any]:
    from polaris.agent.tasks import snapshot

    return snapshot(thread_id)


class AnswerFeedbackIn(BaseModel):
    thread_id: str = Field(..., max_length=64)
    question: str = Field("", max_length=4000)
    answer: str = Field("", max_length=20000)
    verdict: Literal["good", "bad"]
    note: str = Field("", max_length=2000)


FEEDBACK_FILE = "data/feedback/answers.jsonl"


@router.post("/feedback", summary="Mark one answer as good or bad (with a note); saved locally for review")
def answer_feedback_endpoint(body: AnswerFeedbackIn) -> dict[str, Any]:
    import json
    from datetime import UTC, datetime

    from polaris.config import ROOT

    path = ROOT / FEEDBACK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "db": get_settings().database_url.rsplit("/", 1)[-1],
           **body.model_dump()}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"ok": True}

