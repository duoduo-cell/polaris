"""实际图与内存检查点的停止修复；并行写入故障为注入，不启动服务。"""
from datetime import UTC, datetime

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from polaris.agent import main
from polaris.llm.recorder import MemoryRecorder
from polaris.tools.base import ToolSpec
from polaris.tools.runtime import ToolRuntime

CONFIG = {"configurable": {"thread_id": "synthetic-tool-stop"}}


class Args(BaseModel):
    query: str


class Model(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, **kwargs):
        # 模拟聊天协议的独立要求；历史工具调用在下一条用户消息前必须各有结果。
        pending = set()
        for message in messages:
            if message.type in ("human", "ai"):
                assert not pending, f"unanswered calls: {pending}"
                pending = {call["id"] for call in getattr(message, "tool_calls", [])}
            elif message.type == "tool":
                assert message.tool_call_id in pending
                pending.remove(message.tool_call_id)
        assert not pending, f"unanswered calls: {pending}"
        return super()._generate(messages, **kwargs)


def call(cid, query):
    return {"id": cid, "name": "synthetic_query", "args": {"query": query}}


@pytest.fixture
def setup(monkeypatch):
    saver = InMemorySaver()
    effects = []
    monkeypatch.setattr(main, "checkpointer", lambda: saver)
    monkeypatch.setattr(main, "DbRecorder", MemoryRecorder)
    monkeypatch.setattr(main, "_system_prompt", lambda *a: "合成协议检查")
    monkeypatch.setattr(main, "ToolRuntime", lambda **kw: ToolRuntime(**kw, record_db=False))
    monkeypatch.setattr(main, "TOOLS", ("synthetic_query",))
    monkeypatch.setitem(main.REGISTRY, "synthetic_query", ToolSpec(
        "synthetic_query", "合成查询", Args, lambda args: effects.append(args.query) or {"synthetic": args.query}))
    return saver, effects


def graph_with(messages):
    graph = main.build(Model(messages=iter([AIMessage(content="合成新回答")]), disable_streaming=True), [], "合成检查")
    graph.update_state(CONFIG, {"messages": messages}, as_node="model")
    return graph


def test_partial_batch_is_closed_without_changing_returned_result_or_rerunning_tools(setup):
    _, effects = setup
    graph = graph_with([HumanMessage(content="旧请求"), AIMessage(content="", tool_calls=[call("a", "甲"), call("b", "乙")]),
                        ToolMessage(content="甲的已返回证据", tool_call_id="a", name="synthetic_query")])
    main._close_dangling(graph, CONFIG)
    messages = graph.get_state(CONFIG).values["messages"]
    results = {message.tool_call_id: message for message in messages if message.type == "tool"}
    assert set(results) == {"a", "b"} and results["a"].content == "甲的已返回证据"
    assert results["b"].status == "error" and "未确认" in results["b"].content
    checkpoint = graph.get_state(CONFIG).config["configurable"]["checkpoint_id"]
    main._close_dangling(graph, CONFIG)
    assert graph.get_state(CONFIG).config["configurable"]["checkpoint_id"] == checkpoint
    state = graph.invoke({"messages": [HumanMessage(content="新的请求")]}, CONFIG)
    assert state["messages"][-1].content == "合成新回答" and effects == []


def test_old_same_id_result_does_not_answer_a_new_batch(setup):
    graph = graph_with([HumanMessage(content="旧一轮"), AIMessage(content="", tool_calls=[call("same", "旧")]),
                        ToolMessage(content="旧结果", tool_call_id="same", name="synthetic_query"),
                        AIMessage(content="旧正式回答"), HumanMessage(content="新一轮"),
                        AIMessage(content="", tool_calls=[call("same", "新")])])
    main._close_dangling(graph, CONFIG)
    messages = graph.get_state(CONFIG).values["messages"]
    assert messages[-1].type == "tool" and messages[-1].tool_call_id == "same"
    assert messages[-1].status == "error" and "未确认" in messages[-1].content
    assert messages[2].content == "旧结果"


def test_missing_result_is_not_claimed_unexecuted_or_successful(setup):
    graph = graph_with([HumanMessage(content="请求"), AIMessage(content="", tool_calls=[call("missing", "甲")])])
    main._close_dangling(graph, CONFIG)
    result = graph.get_state(CONFIG).values["messages"][-1]
    assert result.type == "tool" and result.status == "error"
    assert "未确认" in result.content and "没有执行。" not in result.content
    assert "不要自动重放" in result.content


@pytest.mark.parametrize("tail", ["complete", "answer", "human"])
def test_repair_does_not_rewrite_completed_or_newer_turns(setup, tail):
    messages = [HumanMessage(content="旧请求"), AIMessage(content="", tool_calls=[call("a", "甲")]),
                ToolMessage(content="已完成", tool_call_id="a", name="synthetic_query")]
    if tail == "answer":
        messages.append(AIMessage(content="正式回答"))
    elif tail == "human":
        messages.append(HumanMessage(content="新问题"))
    graph = graph_with(messages)
    checkpoint = graph.get_state(CONFIG).config["configurable"]["checkpoint_id"]
    main._close_dangling(graph, CONFIG)
    assert graph.get_state(CONFIG).config["configurable"]["checkpoint_id"] == checkpoint


def test_repair_preserves_pending_real_human_approval(setup, monkeypatch):
    _, effects = setup
    monkeypatch.setitem(main.REGISTRY, "synthetic_query", ToolSpec(
        "synthetic_query", "合成待确认操作", Args, lambda args: effects.append(args.query), read_only=False))
    runtime = ToolRuntime(record_db=False)
    model = Model(messages=iter([AIMessage(content="", tool_calls=[call("approval", "甲")])]))
    graph = main.build(model, runtime.langchain_tools([main.REGISTRY["synthetic_query"]]), "合成审批")
    graph.invoke({"messages": [HumanMessage(content="先等本人确认")]}, CONFIG)
    before = graph.get_state(CONFIG)
    assert before.interrupts and effects == []
    main._close_dangling(graph, CONFIG)
    after = graph.get_state(CONFIG)
    assert after.config == before.config and after.interrupts == before.interrupts and effects == []


def test_actual_parallel_result_write_failure_preserves_materialized_results_and_does_not_replay(setup, monkeypatch):
    saver, effects = setup
    original = saver.put_writes
    stopped, faults = [], []

    def fault(config, writes, task_id, task_path=""):
        for channel, value in writes:
            if channel == "messages" and isinstance(value, list) and any(
                    isinstance(message, ToolMessage) and message.tool_call_id == "b" and message.status == "success"
                    for message in value):
                faults.append(True)
                stopped.append(True)
                raise RuntimeError("synthetic checkpoint result write failed")
        return original(config, writes, task_id, task_path)

    monkeypatch.setattr(saver, "put_writes", fault)
    model = Model(messages=iter([AIMessage(content="", tool_calls=[call("a", "甲"), call("b", "乙")]),
                                AIMessage(content="本轮已生成的合成答复")]))
    state, runtime, _, error, _ = main._run({"messages": [HumanMessage(content="并行查询")]}, "synthetic-tool-stop",
                                           datetime(2026, 10, 24, tzinfo=UTC), [], model, None, "synthetic-stop",
                                           should_stop=lambda: bool(stopped))
    assert faults and state is None and error == main.STOPPED
    assert sorted(effects) == ["乙", "甲"] and len(runtime.calls) == 2
    graph = main.build(Model(messages=iter([AIMessage(content="新的回答")])), [], "合成检查")
    messages = graph.get_state(CONFIG).values["messages"]
    results = {message.tool_call_id: message for message in messages if message.type == "tool"}
    # 本次框架仍已物化两条结果；不能把注入写故障直接称为“部分结果丢失”。
    assert set(results) == {"a", "b"} and all(message.status == "success" for message in results.values())
    state = graph.invoke({"messages": [HumanMessage(content="新问题")]}, CONFIG)
    assert state["messages"][-1].content == "新的回答" and len(effects) == 2


def test_stop_after_actual_approval_interrupt_keeps_decision_visible_to_task_finalizer(setup, monkeypatch):
    saver, effects = setup
    monkeypatch.setitem(main.REGISTRY, "synthetic_query", ToolSpec(
        "synthetic_query", "合成待确认操作", Args, lambda args: effects.append(args.query), read_only=False))
    model = Model(messages=iter([AIMessage(content="", tool_calls=[call("approval", "甲")])]))

    def stopped_after_interrupt():
        row = saver.get_tuple(CONFIG)
        return bool(row and any(channel == "__interrupt__" for _, channel, _ in row.pending_writes))

    state, _, _, error, _ = main._run({"messages": [HumanMessage(content="合成确认请求")]}, "synthetic-tool-stop",
                                     datetime(2026, 10, 24, tzinfo=UTC), [], model, None, "synthetic-stop",
                                     should_stop=stopped_after_interrupt)
    assert error == main.STOPPED and effects == []
    assert main._interrupts(state or {})[0]["name"] == "synthetic_query"
