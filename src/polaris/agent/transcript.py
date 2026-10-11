"""真实聊天记录。只在首次访问时从旧检查点恢复，以后不依赖压缩后的上下文。"""
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from polaris.agent.minimal import text_of
from polaris.db.models import ChatMessage, ChatThread
from polaris.db.session import session_scope


def lock_thread(s: Session, thread_id: str) -> None:
    s.execute(text("select pg_advisory_xact_lock(hashtextextended(:thread, 0))"), {"thread": thread_id})


def checkpoint_approvals(saved: Any) -> list[dict[str, Any]]:
    from polaris.agent.main import _interrupts

    waiting = [i for _, channel, value in (saved.pending_writes or []) if channel == "__interrupt__" for i in value]
    return _interrupts({"__interrupt__": waiting})


def ensure_thread(s: Session, thread_id: str) -> ChatThread:
    """调用方要先调过 main.ready()：这里在事务里，第一次建检查点表会自己等自己。"""
    from polaris.agent.main import checkpointer

    lock_thread(s, thread_id)
    thread = s.get(ChatThread, thread_id)
    if thread is None:
        thread = ChatThread(id=thread_id)
        s.add(thread)
        s.flush()
    if thread.history_imported:
        return thread
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    saved = list(checkpointer().list(config))
    seen: set[str] = set()
    # 历史检查点还保有压缩前的消息；按首次出现顺序恢复，消息 ID 去重而不是按文字去重。
    for checkpoint in reversed(saved):
        for m in checkpoint.checkpoint["channel_values"].get("messages", []):
            role = {"human": "user", "ai": "assistant"}.get(m.type)
            content = text_of(m.content)
            if not role or not content or m.additional_kwargs.get("lc_source") == "summarization":
                continue
            if getattr(m, "tool_calls", None):
                continue
            source = str(m.id or f"legacy-{role}-{content}")
            if source in seen:
                continue
            seen.add(source)
            s.add(ChatMessage(thread_id=thread_id, source_key=source[:192], role=role, content=content))
    thread.approvals = checkpoint_approvals(saved[0]) if saved else []
    thread.history_imported = True
    s.flush()
    return thread


def begin_turn(thread_id: str, message: str) -> str:
    from polaris.agent.main import ready

    ready()  # 登记会话前先在事务外建好检查点表（见 main.ready）
    key = str(uuid4())
    with session_scope() as s:
        ensure_thread(s, thread_id)
        s.add(ChatMessage(thread_id=thread_id, source_key=f"{key}:user", role="user", content=message))
    return key


def finish_turn(s: Session, thread_id: str, key: str, turn: dict[str, Any]) -> None:
    thread = ensure_thread(s, thread_id)
    thread.approvals = turn.get("approvals", [])
    source = f"{key}:assistant"
    if "question" not in turn:
        question = s.scalar(select(ChatMessage).where(ChatMessage.thread_id == thread_id, ChatMessage.source_key == f"{key}:user"))
        turn = {**turn, "question": question.content if question else ""}
    if turn.get("answer") and not turn.get("approvals"):
        if s.scalar(select(ChatMessage.id).where(ChatMessage.thread_id == thread_id, ChatMessage.source_key == source)) is None:
            s.add(ChatMessage(thread_id=thread_id, source_key=source, role="assistant", content=turn["answer"], turn=turn))


def visible_messages(s: Session, thread_id: str) -> list[dict[str, Any]]:
    from polaris.followups import states
    from polaris.results import enrich_history

    rows = s.scalars(select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.id)).all()
    for m in rows:
        if m.role == "assistant":
            enrich_history(s, m)
    # 历史答案/来源保持原样，岗位跟进属于当前状态，刷新后不能显示旧的收藏和投递。
    job_ids = {int(r["id"][2:]) for m in rows for r in (m.turn or {}).get("results", [])
               if r.get("kind") == "job" and isinstance(r.get("id"), str) and r["id"].startswith("J-") and r["id"][2:].isdigit()}
    progress = {f"J-{i}": value for i, value in states(s, list(job_ids)).items()}
    out = []
    for m in rows:
        row = {"id": m.id, "role": m.role, "content": m.content}
        if m.role == "user" and m.source_key.endswith(":user"):
            row["task_id"] = m.source_key.removesuffix(":user")  # 网页据此把"已停止 / 出错"显示在这句话下面
        if m.turn:
            cards = [{**r, "followup": progress[r["id"]]} if r.get("kind") == "job" and r.get("id") in progress
                     else r for r in m.turn.get("results", [])]
            row["turn"] = {**m.turn, "results": cards} if "results" in m.turn else m.turn
        out.append(row)
    return out
