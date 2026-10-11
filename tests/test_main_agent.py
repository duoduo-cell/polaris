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
