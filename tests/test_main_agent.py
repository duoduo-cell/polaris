"""主 agent（M6）：先过记忆再回答、有副作用的工具停下来等批准、批准或拒绝后接着走、会话按 thread_id 接着聊。
用按剧本回答的假模型和假的记忆抽取，不调任何真实服务。"""
import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import select

from polaris.agent.main import chat, checkpointer, resume
from polaris.db.models import MemoryFact
from polaris.db.session import session_scope
from polaris.memory.update import Candidate, Context, apply

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)


class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self


def script(*messages: AIMessage) -> ScriptedModel:
    return ScriptedModel(messages=iter(messages))


def call(name: str, args: dict[str, Any], cid: str = "c1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid}])


def fake_remember(text: str, **kw: Any) -> dict[str, Any]:
    with session_scope() as s:
        e, q = apply(Context(s, kw["now"], text), Candidate("school", "某大学", "用户在某大学读书"))
    return {"events": [e], "questions": [q] if q else []}


def test_message_goes_through_memory_then_tools(db):
    tid = f"t-{uuid.uuid4().hex[:8]}"
    turn = chat("我在某大学读书，帮我看看记着什么", tid, now=NOW, remember_fn=fake_remember,
                model=script(call("memory_card", {}), AIMessage(content="记着：学校 某大学（M-1）")))
    assert turn.error is None and turn.answer == "记着：学校 某大学（M-1）"
    assert [e["op"] for e in turn.memory_events] == ["add"]
    assert [c["tool"] for c in turn.tool_calls] == ["memory_card"] and '"某大学"' in turn.tool_calls[0]["output"]


def test_side_effect_tool_waits_for_approval(db):
    tid = f"t-{uuid.uuid4().hex[:8]}"
    turn = chat("把我的学校忘掉", tid, now=NOW, remember_fn=fake_remember,
                model=script(call("memory_forget", {"attribute": "school"})))
    assert turn.answer == "" and turn.approvals[0]["name"] == "memory_forget"
    with session_scope() as s:  # 还没批准，记忆没动
        assert s.execute(select(MemoryFact.status)).scalars().all() == ["active"]
    done = resume(tid, approve=True, now=NOW, model=script(AIMessage(content="已经忘掉了。")))
    assert done.answer == "已经忘掉了。" and [c["tool"] for c in done.tool_calls] == ["memory_forget"]
    with session_scope() as s:
        assert s.execute(select(MemoryFact.status)).scalars().all() == ["retracted"]


def test_rejected_action_does_not_run(db):
    tid = f"t-{uuid.uuid4().hex[:8]}"
    chat("把我的学校忘掉", tid, now=NOW, remember_fn=fake_remember, model=script(call("memory_forget", {"attribute": "school"})))
    done = resume(tid, approve=False, reason="算了，留着吧", now=NOW, model=script(AIMessage(content="好，不删。")))
    assert done.answer == "好，不删。" and done.tool_calls == []
    with session_scope() as s:
        assert s.execute(select(MemoryFact.status)).scalars().all() == ["active"]


def test_thread_is_kept_between_turns(db):
    tid = f"t-{uuid.uuid4().hex[:8]}"
    chat("你好", tid, now=NOW, remember_fn=lambda *a, **k: {"events": [], "questions": []},
         model=script(AIMessage(content="你好！")))
    chat("刚才我说了什么？", tid, now=NOW, remember_fn=lambda *a, **k: {"events": [], "questions": []},
         model=script(AIMessage(content="你说了你好。")))
    saved = checkpointer().get_tuple({"configurable": {"thread_id": tid}})
    texts = [m.content for m in saved.checkpoint["channel_values"]["messages"]]
    assert texts == ["你好", "你好！", "刚才我说了什么？", "你说了你好。"]


def test_messages_endpoint_reads_the_thread(db):
    from fastapi.testclient import TestClient

    from polaris.api.app import app

    tid = f"t-{uuid.uuid4().hex[:8]}"
    chat("你好", tid, now=NOW, remember_fn=lambda *a, **k: {"events": [], "questions": []}, model=script(AIMessage(content="你好！")))
    r = TestClient(app).get(f"/api/me/chat/{tid}/messages", headers={"Authorization": "Bearer test-owner-token"}).json()
    assert r["messages"] == [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好！"}]


def test_messages_endpoint_shows_pending_approval(db):
    from fastapi.testclient import TestClient

    from polaris.api.app import app

    tid = f"t-{uuid.uuid4().hex[:8]}"
    chat("把我的学校忘掉", tid, now=NOW, remember_fn=fake_remember, model=script(call("memory_forget", {"attribute": "school"})))
    r = TestClient(app).get(f"/api/me/chat/{tid}/messages", headers={"Authorization": "Bearer test-owner-token"}).json()
    assert r["approvals"][0]["name"] == "memory_forget"


def test_streamed_turn_emits_memory_tool_tokens_then_done(db):
    import json

    from fastapi.testclient import TestClient

    from polaris.agent.main import streamed
    from polaris.api.app import app

    tid = f"t-{uuid.uuid4().hex[:8]}"
    model = script(call("memory_card", {}), AIMessage(content="记着：学校 某大学"))
    model.disable_streaming = True  # 假模型流式时会丢掉工具调用（真的 ChatOpenAI 不会），这里整条返回
    events = list(streamed(chat, "看看记着什么", tid, now=NOW, remember_fn=fake_remember, model=model))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "memory" and kinds[-1] == "done" and "tool" in kinds
    assert kinds.index("tool") < kinds.index("token")
    assert "".join(e["text"] for e in events if e["type"] == "token") == events[-1]["turn"]["answer"] == "记着：学校 某大学"

    tid2 = f"t-{uuid.uuid4().hex[:8]}"
    from polaris.agent import main

    real_chat = main.chat
    main.chat = lambda message, thread_id, on_event=None: real_chat(  # noqa: E731
        message, thread_id, now=NOW, remember_fn=fake_remember, model=script(AIMessage(content="好的")), on_event=on_event)
    try:
        r = TestClient(app).post("/api/me/chat/stream", json={"message": "你好", "thread_id": tid2},
                                 headers={"Authorization": "Bearer test-owner-token"})
    finally:
        main.chat = real_chat
    lines = [json.loads(x[6:]) for x in r.text.split("\n\n") if x.startswith("data: ")]
    assert r.headers["content-type"].startswith("text/event-stream") and lines[-1]["turn"]["answer"] == "好的"
