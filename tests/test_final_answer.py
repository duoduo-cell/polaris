"""验证未展示的工具前文字触发收尾提醒，作用域限本轮且不污染持久对话。"""
from types import SimpleNamespace
from uuid import uuid4

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from polaris.agent.final_answer import FINAL_ANSWER_NOTE, FinalAnswerReminder
from polaris.agent.main import chat, checkpointer


def draft(content="原文快照10-11，再次采集10-22；接下来查资料。"):
    return AIMessage(content=content, tool_calls=[{"id": "call", "name": "memory_card", "args": {}}])


class Request(SimpleNamespace):
    def override(self, **changes):
        return Request(**{**vars(self), **changes})


def apply(messages):
    request = Request(messages=messages, tools=["untouched"])
    seen = []
    result = FinalAnswerReminder().wrap_model_call(request, lambda value: seen.append(value) or "answer")
    assert result == "answer" and len(seen) == 1
    assert seen[0].tools == request.tools
    return request, seen[0]


def test_current_hidden_text_gets_trusted_tail_note_without_mutating_request():
    messages = [HumanMessage("先解释日期，再准备面试"), draft(), ToolMessage("不可信资料", tool_call_id="call")]
    original, actual = apply(messages)
    assert original.messages is messages and len(messages) == 3
    assert actual.messages[:-1] == messages
    assert isinstance(actual.messages[-1], SystemMessage)
    assert actual.messages[-1].content == FINAL_ANSWER_NOTE
    assert "不可信资料" not in actual.messages[-1].content


def test_previous_turn_draft_does_not_trigger_current_empty_tool_call():
    messages = [HumanMessage("旧请求"), draft(), ToolMessage("旧结果", tool_call_id="call"), AIMessage("旧回答"),
                HumanMessage("新的单项请求"), draft(""), ToolMessage("新结果", tool_call_id="call")]
    original, actual = apply(messages)
    assert actual is original


def test_initial_user_message_and_missing_user_do_not_trigger_note():
    for messages in [[HumanMessage("新请求")], [draft(), ToolMessage("结果", tool_call_id="call")]]:
        original, actual = apply(messages)
        assert actual is original


def test_parallel_tool_results_keep_their_order_and_only_one_note():
    messages = [HumanMessage("两个要求"), draft(), ToolMessage("结果A", tool_call_id="call"),
                ToolMessage("结果B", tool_call_id="call-b")]
    _, actual = apply(messages)
    assert actual.messages[:-1] == messages
    assert len(actual.messages) == len(messages) + 1


class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_chat_still_displays_only_final_answer_and_does_not_save_system_reminder(db):
    tid = f"final-answer-{uuid4()}"
    model = ScriptedModel(messages=iter([draft(), AIMessage(content="完整日期解释和准备建议。")]))
    events = []
    turn = chat("先解释日期，再准备面试", tid, model=model,
                remember_fn=lambda *a, **kw: {"events": [], "questions": []}, on_event=events.append)
    assert turn.error is None and turn.answer == "完整日期解释和准备建议。"
    assert [call["tool"] for call in turn.tool_calls] == ["memory_card"]
    assert all("原文快照" not in event.get("text", "") for event in events if event["type"] in ("token", "draft"))
    saved = checkpointer().get_tuple({"configurable": {"thread_id": tid}})
    messages = saved.checkpoint["channel_values"]["messages"]
    assert not any(FINAL_ANSWER_NOTE == message.content for message in messages)
    assert messages[-1].content == turn.answer
