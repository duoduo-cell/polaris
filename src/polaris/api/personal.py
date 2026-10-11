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


@router.post("/chat/tasks/{task_id}/cancel", summary="Stop a queued or running answer; the message itself is kept")
def cancel_task(task_id: str) -> dict[str, Any]:
    from polaris.agent.tasks import cancel, service

    try:
        out = cancel(task_id)
    except LookupError as e:
        raise HTTPException(404, "task not found") from e
    service().wake.set()
    return out


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

    out = memory_card(MemoryCardArgs(on=on))
    from polaris.db.models import Job
    from polaris.results import REF_RE

    ids = {int(ref[2:]) for f in out["facts"] for ref in REF_RE.findall(f.get("for", "")) if ref.startswith("J-")}
    if ids:
        with session_scope() as s:
            names = {f"J-{j.id}": j.title for j in s.scalars(select(Job).where(Job.id.in_(ids))).all()}
        for fact in out["facts"]:
            fact["scope_label"] = REF_RE.sub(lambda m: names.get(m[0], m[0]), fact.get("for", ""))
    return out


@router.get("/results/{reference}", summary="Read a result directly, with full original sources and current followup state")
def result_detail(reference: str) -> dict[str, Any]:
    from polaris.results import REF_RE, entities

    if not REF_RE.fullmatch(reference):
        raise HTTPException(422, "无效的结果编号")
    with session_scope() as s:
        rows = entities(s, [reference], detailed=True)
        if not rows:
            raise HTTPException(404, "这条资料已经不存在，请重新搜索")
        return rows[0]


class FollowupChanges(BaseModel):
    saved: bool | None = None
    status: Literal["untracked", "skipped", "applied", "interview", "offer", "rejected", "withdrawn"] | None = None
    stage: str | None = Field(None, max_length=200)
    note: str | None = Field(None, max_length=2000)
    next_followup: date | None = None


class FollowupIn(BaseModel):
    request_id: UUID
    revision: int | None = Field(None, ge=0)
    changes: FollowupChanges


@router.post("/jobs/{reference}/followup", summary="An explicit user action updates one job; retries are idempotent")
def update_followup(reference: str, body: FollowupIn) -> dict[str, Any]:
    from polaris.followups import FollowupConflict, update
    from polaris.tools.base import ToolError
    from polaris.tools.jobs import _parse

    changes = body.changes.model_dump(exclude_unset=True)
    if any(changes.get(k) is None for k in ("saved", "status") if k in changes):
        raise HTTPException(422, "收藏和投递状态不能留空")
    try:
        job_id = _parse(reference, "J", "jobs_search", '{"job_id": "J-12"}')
        with session_scope() as s:
            return update(s, job_id, changes, str(body.request_id), "用户在岗位详情中明确保存了状态", body.revision)
    except FollowupConflict as e:
        raise HTTPException(409, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except (ValueError, ToolError) as e:
        raise HTTPException(422, str(e)) from e


@router.get("/followups", summary="Saved jobs and independent application progress")
def followups_endpoint() -> list[dict[str, Any]]:
    from sqlalchemy import or_

    from polaris.db.models import JobFollowup
    from polaris.results import entities

    with session_scope() as s:
        stmt = select(JobFollowup).where(or_(JobFollowup.saved.is_(True), JobFollowup.status != "untracked",
                                             JobFollowup.stage.is_not(None), JobFollowup.note.is_not(None),
                                             JobFollowup.next_followup.is_not(None)))
        rows = s.scalars(stmt.order_by(JobFollowup.updated_at.desc()).limit(100)).all()
        return entities(s, [f"J-{r.job_id}" for r in rows])


@router.get("/knowledge/posts", summary="Browse source-backed interview, referral and recruiting posts without model calls")
def browse_posts(company: str | None = None, post_type: str | None = None, material: str | None = None,
                 role: str | None = None, job_type: str | None = None, source: str | None = None,
                 text: str | None = None, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    from polaris.results import POST_KINDS
    from polaris.tools.jobs import PostsSearchArgs, posts_search

    try:
        args = PostsSearchArgs(company=company, post_type=post_type, material=material, role=role, job_type=job_type,
                               source=source, keyword=text, limit=limit, offset=offset)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    out = posts_search(args)
    for row in out["items"]:
        row["kind"] = POST_KINDS.get(row["type"], "post")
        if row["kind"] == "interview" and row["material"] != "firsthand":
            row["kind"] = "study" if row["material"] == "study" else "post"
    return out


@router.get("/knowledge/questions", summary="Browse questions by actual interview company, role, round and material")
def browse_questions(company: str | None = None, role: str | None = None, job_type: str | None = None,
                     round: str | None = None, material: str | None = None, category: str | None = None,
                     source: str | None = None, text: str | None = None, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    from polaris.companies import match
    from polaris.db.models import InterviewQuestion, Post, RawItem
    from polaris.tools.knowledge import question_row

    if not 1 <= limit <= 50 or offset < 0 or material not in (None, "firsthand", "study", "uncertain"):
        raise HTTPException(422, "筛选或分页参数不合法")
    with session_scope() as s:
        stmt = select(InterviewQuestion, Post, RawItem).join(Post, Post.id == InterviewQuestion.post_id)
        stmt = stmt.join(RawItem, RawItem.id == Post.raw_item_id).where(RawItem.is_synthetic.is_(False))
        if company:
            big = match(company)
            stmt = stmt.where(InterviewQuestion.company == (big.name if big else company))
        if role:
            stmt = stmt.where(InterviewQuestion.role.ilike(f"%{role}%"))
        for attr, value in (("job_type", job_type), ("round", round), ("material", material), ("category", category)):
            if value:
                stmt = stmt.where(getattr(InterviewQuestion, attr) == value)
        if source:
            stmt = stmt.where(RawItem.source == source)
        if text:
            stmt = stmt.where(InterviewQuestion.question.ilike(f"%{text}%"))
        rows = s.execute(stmt.order_by(InterviewQuestion.published_at.desc().nulls_last(), InterviewQuestion.id)
                         .offset(offset).limit(limit + 1)).all()
        return {"items": [{**question_row(q, p, r), "kind": "question"} for q, p, r in rows[:limit]],
                "next_offset": offset + limit if len(rows) > limit else None}


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


@router.get("/agenda", summary="What Polaris is doing, needs from you, has scheduled and finished today")
def agenda_endpoint() -> dict[str, Any]:
    from polaris.agenda import agenda

    return agenda()


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
        result: Any = runtime.results[len(runtime.calls) - 1] if len(runtime.calls) - 1 in runtime.results else json.loads(out)
    except ValueError:
        result = out
    if call["ok"] and name in ("jobs_search", "jobs_get") and isinstance(result, dict):
        result = _with_match(result)
    return {"ok": call["ok"], "error_kind": call["error_kind"], "result": result}


def _with_match(result: dict[str, Any]) -> dict[str, Any]:
    """网页直接查岗位时，每个岗位附上和你条件的匹配（模型看到的工具结果里没有这个）。"""
    from polaris.match import matches

    rows = result.get("items") if isinstance(result.get("items"), list) else [result]
    ids = [int(r["id"][2:]) for r in rows if isinstance(r, dict) and str(r.get("id", "")).startswith("J-") and r["id"][2:].isdigit()]
    with session_scope() as s:
        fits = matches(s, ids)
    if not fits:
        return result
    marked = [{**r, "match": fits[int(r["id"][2:])]} if isinstance(r, dict) and str(r.get("id", ""))[2:].isdigit()
              and int(r["id"][2:]) in fits else r for r in rows]
    return {**result, "items": marked} if "items" in result else marked[0]


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

