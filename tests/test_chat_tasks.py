"""独立测试库和假模型：压缩隔离、历史恢复、断开后执行、排队、审批、去重和故障。"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from sqlalchemy import select

from polaris.agent import main
from polaris.agent.main import Turn
from polaris.agent.tasks import TaskConflict, service, snapshot, submit
from polaris.api.app import app
from polaris.db.models import ChatTask
from polaris.db.session import session_scope
from test_main_agent import script

AUTH = {"Authorization": "Bearer test-owner-token"}


def tid():
    return f"t-{uuid4().hex}"


def wait_for(thread_id, state="completed", count=1):
    until = time.monotonic() + 6
    while time.monotonic() < until:
        result = snapshot(thread_id)
        if sum(t["state"] == state for t in result["tasks"]) >= count:
            return result
        time.sleep(0.03)
    raise AssertionError(result)


def fake_chat(message, thread_id, on_event=None, **kw):
    if on_event:
        on_event({"type": "token", "text": "内部套话不能出现在聊天"})
        on_event({"type": "tool", "tool": "jobs_search", "args": {"query": message}})
    return Turn(thread_id, f"正式回复：{message}")


def test_compaction_never_replaces_actual_transcript(db):
    thread = tid()
    remember = lambda *a, **k: {"events": [], "questions": ["以北京为准吗？"]}  # noqa: E731
    main.chat("  原话\n第二行  ", thread, model=script(AIMessage(content="回复")), remember_fn=remember)
    graph = main.build(script(AIMessage(content="unused")), [], "test")
    graph.update_state({"configurable": {"thread_id": thread}}, {"messages": [
        RemoveMessage(id=REMOVE_ALL_MESSAGES),
        HumanMessage(content="Here is a summary of the conversation to date: 内部摘要",
                     additional_kwargs={"lc_source": "summarization"}), AIMessage(content="压缩后上下文"),
    ]})
    result = snapshot(thread)
    assert [(m["role"], m["content"]) for m in result["messages"]] == [
        ("user", "  原话\n第二行  "), ("assistant", "回复\n\n---\n顺便确认一下：以北京为准吗？")]
    assert "内部摘要" not in str(result["messages"])


def test_legacy_history_recovers_originals_from_older_checkpoints(db, monkeypatch):
    first = HumanMessage(content="我真正说的话", id="h1")
    answer = AIMessage(content="正式回复", id="a1")
    preamble = AIMessage(content="我先去查查", id="t1",
                         tool_calls=[{"id": "tc", "name": "lookup", "args": {}}])
    summary = HumanMessage(content="内部摘要", id="summary", additional_kwargs={"lc_source": "summarization"})
    history = [
        SimpleNamespace(checkpoint={"channel_values": {"messages": [summary, HumanMessage(content="我真正说的话", id="h2")]}},
                        pending_writes=[]),
        SimpleNamespace(checkpoint={"channel_values": {"messages": [first, preamble, answer]}}, pending_writes=[]),
        SimpleNamespace(checkpoint={"channel_values": {"messages": [first]}}, pending_writes=[]),
    ]
    monkeypatch.setattr(main, "checkpointer", lambda: SimpleNamespace(list=lambda cfg: iter(history)))
    thread = tid()
    assert [m["content"] for m in snapshot(thread)["messages"]] == ["我真正说的话", "正式回复", "我真正说的话"]
    assert len(snapshot(thread)["messages"]) == 3  # 导入幂等，相同原话不同 ID 不能合并


def test_task_runs_after_request_client_closes_and_result_survives(db, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def delayed(message, thread_id, **kw):
        entered.set()
        assert release.wait(5)
        return fake_chat(message, thread_id, **kw)

    monkeypatch.setattr(main, "chat", delayed)
    thread, request = tid(), str(uuid4())
    client = TestClient(app)
    r = client.post("/api/me/chat/tasks", json={"message": "真实问题", "thread_id": thread, "request_id": request}, headers=AUTH)
    assert r.status_code == 202 and entered.wait(3)
    client.close()
    assert snapshot(thread)["tasks"][0]["state"] == "running"
    release.set()
    result = wait_for(thread)
    assert [m["content"] for m in result["messages"]] == ["真实问题", "正式回复：真实问题"]
    assert "内部套话" not in str(result)
    assert result["tasks"][0]["activities"][0]["tool"] == "jobs_search"
    other = TestClient(app).get(f"/api/me/chat/tasks/{r.json()['id']}", headers=AUTH)
    assert other.json()["result"]["answer"] == "正式回复：真实问题"


def test_same_thread_serializes_and_other_thread_runs_in_parallel(db, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def delayed(message, thread_id, **kw):
        calls.append(message)
        if message == "first":
            entered.set()
            assert release.wait(5)
        return fake_chat(message, thread_id, **kw)

    monkeypatch.setattr(main, "chat", delayed)
    thread, other = tid(), tid()
    submit(thread, "chat", {"message": "first"}, str(uuid4()))
    assert entered.wait(3)
    submit(thread, "chat", {"message": "second"}, str(uuid4()))
    submit(other, "chat", {"message": "other"}, str(uuid4()))
    wait_for(other)
    assert calls == ["first", "other"]
    assert [t["state"] for t in snapshot(thread)["tasks"]] == ["running", "queued"]
    release.set()
    wait_for(thread, count=2)
    assert calls == ["first", "other", "second"]
    answers = [m for m in snapshot(thread)["messages"] if m["role"] == "assistant"]
    assert [m["turn"]["question"] for m in answers] == ["first", "second"]


def test_concurrent_duplicate_requests_run_once(db, monkeypatch):
    calls = []
    monkeypatch.setattr(main, "chat", lambda message, thread_id, **kw: (calls.append(message) or Turn(thread_id, "完成")))
    thread, key = tid(), str(uuid4())
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: submit(thread, "chat", {"message": "相同提交"}, key), range(5)))
    assert len({t["id"] for t in results}) == 1
    result = wait_for(thread)
    assert calls == ["相同提交"] and len(result["messages"]) == 2
    with pytest.raises(TaskConflict):
        submit(thread, "chat", {"message": "不同内容"}, key)


def test_approval_survives_reconnect_and_duplicate_decision_is_safe(db, monkeypatch):
    approval = {"name": "memory_forget", "args": {"attribute": "school"}, "description": "忘掉学校"}
    monkeypatch.setattr(main, "chat", lambda message, thread_id, **kw: Turn(thread_id, "", approvals=[approval]))
    decisions = []

    def resume(thread_id, approve, reason, **kw):
        decisions.append(approve)
        return Turn(thread_id, "已经忘掉" if approve else "保留了")

    monkeypatch.setattr(main, "resume", resume)
    thread = tid()
    submit(thread, "chat", {"message": "忘掉学校"}, str(uuid4()))
    result = wait_for(thread, "waiting_approval")
    assert result["approvals"] == [approval] and len(result["messages"]) == 1
    with pytest.raises(TaskConflict):
        submit(thread, "chat", {"message": "不能跳过批准"}, str(uuid4()))
    request = str(uuid4())
    a = submit(thread, "resume", {"approve": False, "reason": "保留"}, request)
    b = submit(thread, "resume", {"approve": False, "reason": "保留"}, request)
    assert a["id"] == b["id"]
    result = wait_for(thread)
    assert decisions == [False] and result["approvals"] == []
    assert [m["content"] for m in result["messages"]] == ["忘掉学校", "保留了"]


def test_error_is_durable_and_next_message_can_run(db, monkeypatch):
    def fail(*a, **kw):
        raise RuntimeError("模拟模型失败")

    monkeypatch.setattr(main, "chat", fail)
    thread = tid()
    submit(thread, "chat", {"message": "失败的请求"}, str(uuid4()))
    result = wait_for(thread, "failed")
    assert len(result["messages"]) == 1 and "模拟模型失败" in result["tasks"][0]["error"]
    monkeypatch.setattr(main, "chat", fake_chat)
    submit(thread, "chat", {"message": "再问一次"}, str(uuid4()))
    assert wait_for(thread)["messages"][-1]["content"] == "正式回复：再问一次"


def test_restart_marks_unfinished_work_and_runs_queued_work(db, monkeypatch):
    monkeypatch.setattr(main, "chat", fake_chat)
    thread = tid()
    abandoned = submit(thread, "chat", {"message": "已运行"}, str(uuid4()), start=False)
    submit(thread, "chat", {"message": "还在排队"}, str(uuid4()), start=False)
    with session_scope() as s:
        s.get(ChatTask, abandoned["id"]).state = "running"
    service()
    result = wait_for(thread)
    assert result["tasks"][0]["state"] == "interrupted"
    assert "后端重启" in result["tasks"][0]["error"]
    assert [m["content"] for m in result["messages"]] == ["已运行", "还在排队", "正式回复：还在排队"]


def test_tasks_require_owner_and_validate_requests(db):
    client = TestClient(app)
    assert client.post("/api/me/chat/tasks", json={}).status_code == 401
    assert client.get("/api/me/chat/threads").status_code == 401
    assert client.get("/api/me/chat/tasks/missing", headers=AUTH).status_code == 404
    for message, key in [("   ", str(uuid4())), ("hello", "invalid")]:
        assert client.post("/api/me/chat/tasks", json={"message": message, "request_id": key}, headers=AUTH).status_code == 422
    assert client.post(f"/api/me/chat/{tid()}/resume/tasks",
                       json={"approve": True, "request_id": str(uuid4())}, headers=AUTH).status_code == 409


def test_legacy_stream_does_not_publish_tool_preamble(db):
    model = script(AIMessage(content="我先查一下内部步骤", tool_calls=[{"name": "memory_card", "args": {}, "id": "tc"}]),
                   AIMessage(content="正式回答"))
    model.disable_streaming = True
    events = list(main.streamed(main.chat, "看看条件", tid(), model=model,
                               remember_fn=lambda *a, **k: {"events": [], "questions": []}))
    assert "".join(e["text"] for e in events if e["type"] == "token") == "正式回答"


def test_parallel_memory_writers_do_not_create_duplicate_facts(db):
    from datetime import UTC, datetime

    from polaris.db.models import MemoryFact
    from polaris.memory.update import Candidate, Context, apply

    entered, release = threading.Event(), threading.Event()

    def write(hold=False):
        with session_scope() as s:
            event, _ = apply(Context(s, datetime.now(UTC), "我在某大学读书"),
                             Candidate("school", "某大学", "用户在某大学读书"))
            if hold:
                entered.set()
                assert release.wait(3)
            return event["op"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(write, True)
        assert entered.wait(2)
        second = pool.submit(write)
        time.sleep(0.1)
        assert not second.done()
        release.set()
        assert (first.result(), second.result()) == ("add", "noop")
    with session_scope() as s:
        assert len(s.scalars(select(MemoryFact).where(MemoryFact.status == "active")).all()) == 1
