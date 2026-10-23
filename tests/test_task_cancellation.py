"""任务停止的确定性时序检查；SQLite仅承载ORM状态，不模拟PG锁或启动后台服务。"""
import threading
from contextlib import contextmanager

import pytest
from sqlalchemy import JSON, BigInteger, Integer, MetaData, create_engine, event, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from polaris.agent import main, tasks, transcript
from polaris.agent.main import Turn
from polaris.db.models import ChatMessage, ChatTask, ChatThread
from polaris.db.session import session_scope


@pytest.fixture
def queue(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    # 只建三个聊天表的临时投影，不执行生产迁移；SQLite无JSONB／BIGSERIAL／PG默认值。
    for model in (ChatThread, ChatTask, ChatMessage):
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
            elif isinstance(column.type, BigInteger):
                column.type = Integer()
            if column.name == "live":
                column.server_default = None
    metadata.create_all(engine)
    with session_scope(engine) as session:
        session.add(ChatThread(id="synthetic-thread", history_imported=True, approvals=[]))
        session.add(ChatTask(id="synthetic-task", thread_id="synthetic-thread", request_key="synthetic-request",
                             kind="chat", payload={"message": "合成求职请求"}, state="running"))

    @contextmanager
    def scope(_engine=None):
        with session_scope(engine) as session:
            yield session

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "lock_thread", lambda *a: None)
    monkeypatch.setattr(transcript, "lock_thread", lambda *a: None)
    instance = object.__new__(tasks.TaskService)  # 构造器会启动线程，离线检查不调用它。
    instance.engine, instance.wake = engine, threading.Event()
    yield engine, instance
    engine.dispose()


def saved(engine):
    with session_scope(engine) as session:
        task = session.get(ChatTask, "synthetic-task")
        return tasks.task_dict(task), session.scalars(select(ChatMessage)).all()


def late_turn(monkeypatch, engine, *, kind="chat", approvals=None):
    with session_scope(engine) as session:
        task = session.get(ChatTask, "synthetic-task")
        task.kind = kind
        if kind == "resume":
            task.payload = {"approve": False, "reason": "合成拒绝"}

    def finish(*a, **kw):
        assert kw["should_stop"]() is False  # 最后一次轮询还没见到停止。
        with session_scope(engine) as session:
            session.get(ChatTask, "synthetic-task").cancel_requested = True
        return Turn("synthetic-thread", "不应交付的迟到报告", approvals=approvals or [],
                    results=[{"id": "J-1", "kind": "job"}], llm_call_ids=[41, 42], latency_ms=25,
                    memory_events=[{"type": "synthetic-already-applied"}],
                    tool_calls=[{"tool": "synthetic-query", "args": {}, "ok": True}],
                    trace={"models": [{"input_tokens": 7, "output_tokens": 2, "cost_usd": None}]})

    monkeypatch.setattr(main, "chat" if kind == "chat" else "resume", finish)


@pytest.mark.parametrize("kind", ["chat", "resume"])
def test_stop_after_last_poll_suppresses_late_answer_but_keeps_actual_usage(queue, monkeypatch, kind):
    engine, instance = queue
    late_turn(monkeypatch, engine, kind=kind)
    instance._work("synthetic-task")
    task, messages = saved(engine)
    assert task["state"] == "cancelled" and task["error"] == main.STOPPED
    assert task["result"]["answer"] == "" and task["result"]["results"] == []
    assert task["result"]["error"] == main.STOPPED and messages == []
    assert task["result"]["llm_call_ids"] == [41, 42]
    assert task["result"]["trace"]["models"][0]["cost_usd"] is None
    assert task["result"]["tool_calls"][0]["ok"] is True
    assert task["result"]["memory_events"] == [{"type": "synthetic-already-applied"}]
    assert task["live"] == {} and instance.wake.is_set()


def test_stop_while_finalizer_waits_for_thread_lock_is_observed(queue, monkeypatch):
    engine, instance = queue
    monkeypatch.setattr(main, "chat", lambda *a, **kw: Turn("synthetic-thread", "迟到报告", llm_call_ids=[41]))

    def stop_on_lock(session, thread_id):
        # 持有的ORM对象仍旧；模拟另一个事务在获得会话锁之前提交停止。
        session.connection().execute(update(ChatTask).where(ChatTask.id == "synthetic-task").values(cancel_requested=True))

    monkeypatch.setattr(tasks, "lock_thread", stop_on_lock)
    instance._work("synthetic-task")
    task, messages = saved(engine)
    assert task["state"] == "cancelled" and messages == []
    assert task["result"]["llm_call_ids"] == [41]


def test_claim_does_not_restart_a_task_cancelled_while_waiting_for_lock(queue, monkeypatch):
    engine, instance = queue
    with session_scope(engine) as session:
        session.get(ChatTask, "synthetic-task").state = "queued"

    def cancelled_on_lock(session, thread_id):
        session.connection().execute(update(ChatTask).where(ChatTask.id == "synthetic-task").values(state="cancelled"))

    monkeypatch.setattr(tasks, "lock_thread", cancelled_on_lock)
    assert instance._claim() is None
    task, _ = saved(engine)
    assert task["state"] == "cancelled" and task["started_at"] is None


@pytest.mark.parametrize("next_state", ["running", "completed"])
def test_cancel_reloads_state_after_waiting_for_thread_lock(queue, monkeypatch, next_state):
    engine, _ = queue
    with session_scope(engine) as session:
        session.get(ChatTask, "synthetic-task").state = "queued"

    def transition_on_lock(session, thread_id):
        session.connection().execute(update(ChatTask).where(ChatTask.id == "synthetic-task").values(state=next_state))

    monkeypatch.setattr(tasks, "lock_thread", transition_on_lock)
    response = tasks.cancel("synthetic-task")
    assert response["state"] == next_state
    assert response["cancel_requested"] is (next_state == "running")
    assert response["error"] is None


def test_completed_answer_stays_saved_if_completion_wins_before_stop(queue, monkeypatch):
    engine, instance = queue
    monkeypatch.setattr(main, "chat", lambda *a, **kw: Turn("synthetic-thread", "正式合成报告", llm_call_ids=[41]))
    instance._work("synthetic-task")
    response = tasks.cancel("synthetic-task")
    task, messages = saved(engine)
    assert response["state"] == task["state"] == "completed"
    assert response["cancel_requested"] is False
    assert [message.content for message in messages] == ["正式合成报告"]


def test_pending_approval_is_preserved_when_stop_arrives_late(queue, monkeypatch):
    engine, instance = queue
    approval = {"name": "memory_forget", "args": {"attribute": "school"}, "description": "合成待确认动作"}
    late_turn(monkeypatch, engine, approvals=[approval])
    instance._work("synthetic-task")
    task, messages = saved(engine)
    # 停止回答不替用户批准／拒绝已挂起的动作，也不让它消失后挡住下一次对话。
    assert task["state"] == "waiting_approval" and task["error"] == main.STOPPED
    assert task["result"]["answer"] == "" and messages == []
    with session_scope(engine) as session:
        assert session.get(ChatThread, "synthetic-thread").approvals == [approval]


@pytest.mark.parametrize("terminal", ["cancelled", "completed"])
def test_late_event_cannot_restore_draft_after_task_finishes(queue, monkeypatch, terminal):
    engine, instance = queue

    def completed_after_load(session, obj):
        if isinstance(obj, ChatTask):
            # 直接改变数据库，保留刚读取的identity-map旧对象；不依赖被测代码是否取锁。
            session.connection().execute(update(ChatTask).where(ChatTask.id == obj.id).values(state=terminal, live={}))

    event.listen(Session, "loaded_as_persistent", completed_after_load)
    try:
        instance._event("synthetic-task", {"type": "draft", "text": "迟到草稿"})
    finally:
        event.remove(Session, "loaded_as_persistent", completed_after_load)
    task, _ = saved(engine)
    assert task["state"] == terminal and task["live"] == {}


def test_stop_clears_preview_and_ignores_later_drafts_but_keeps_tool_activity(queue):
    engine, instance = queue
    instance._event("synthetic-task", {"type": "draft", "text": "停止前草稿"})
    response = tasks.cancel("synthetic-task")
    assert response["live"] == {} and response["cancel_requested"] is True
    instance._event("synthetic-task", {"type": "draft", "text": "停止后迟到草稿"})
    instance._event("synthetic-task", {"type": "compacting"})
    instance._event("synthetic-task", {"type": "tool_done", "tool": "synthetic-query", "ok": True})
    task, _ = saved(engine)
    assert not task["live"].get("draft") and not task["live"].get("status")
    assert task["activities"][-1]["tool"] == "synthetic-query"


def test_cancel_during_exception_is_not_overwritten_as_generic_failure(queue, monkeypatch):
    engine, instance = queue

    def fail(*a, **kw):
        with session_scope(engine) as session:
            session.get(ChatTask, "synthetic-task").cancel_requested = True
        raise RuntimeError("合成流式连接中断")

    monkeypatch.setattr(main, "chat", fail)
    instance._work("synthetic-task")
    task, messages = saved(engine)
    assert task["state"] == "cancelled" and task["error"] == main.STOPPED
    assert messages == [] and task["live"] == {}


def test_unrequested_exception_remains_a_failure(queue, monkeypatch):
    engine, instance = queue

    def fail(*a, **kw):
        raise RuntimeError("合成连接故障")

    monkeypatch.setattr(main, "chat", fail)
    instance._work("synthetic-task")
    task, messages = saved(engine)
    assert task["state"] == "failed" and "合成连接故障" in task["error"]
    assert messages == [] and task["live"] == {}
