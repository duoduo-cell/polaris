"""实际LangGraph／内存检查点与临时ORM任务的交付边界；不启动服务或调用真实模型。"""
from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field
from sqlalchemy import select

from polaris.agent import main, report_workflow, task_delivery, transcript
from polaris.db import session as db_session
from polaris.db.models import ChatMessage, ChatTask, ChatThread
from polaris.llm.recorder import MemoryRecorder
from polaris.tools.base import FinalResponse, ToolSpec
from polaris.tools.runtime import ToolRuntime
from test_task_cancellation import queue as queue_fixture

queue = queue_fixture  # 仅复用不启动调度线程的内存ORM夹具。
NOW = datetime(2026, 10, 24, tzinfo=UTC)
TASK_ID = "polaris_task_id"
REPORT = "未交付的合成报告：应当选择演示岗位甲。"


class Model(GenericFakeChatModel):
    seen: list[list[Any]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, **kwargs)


class Args(BaseModel):
    pass


def setup(monkeypatch, engine):
    saver = InMemorySaver()
    monkeypatch.setattr(db_session, "get_engine", lambda *a: engine)
    monkeypatch.setattr(main, "checkpointer", lambda: saver)
    monkeypatch.setattr(main, "DbRecorder", MemoryRecorder)
    monkeypatch.setattr(main, "_system_prompt", lambda *a: "合成任务边界测试")
    monkeypatch.setattr(main, "ToolRuntime", lambda **kw: ToolRuntime(**kw, record_db=False))
    monkeypatch.setattr(report_workflow, "report_tool", lambda *a: ToolSpec(
        "career_report", "合成报告", Args, lambda args: FinalResponse(REPORT, "ready"), return_direct=True))
    return saver


def run(model, content, **kwargs):
    return main._run({"messages": [HumanMessage(content=content)]}, "synthetic-thread", NOW, [], model, None,
                     "synthetic-delivery", **kwargs)


def test_saved_but_undelivered_report_is_excluded_from_actual_next_model_request(queue, monkeypatch):
    engine, _ = queue
    saver = setup(monkeypatch, engine)
    first = Model(messages=iter([AIMessage(content="", tool_calls=[{
        "id": "report-first", "name": "career_report", "args": {}}])]))
    state, _, _, error, _ = run(first, "生成合成报告")
    assert error is None and state["messages"][-1].content == REPORT
    # 将归属标签加到真实图产出的消息，单独检查“有归属仍错误带入”的旧实现。
    messages = [m.model_copy(update={"additional_kwargs": {**m.additional_kwargs, TASK_ID: "synthetic-task"}})
                if m.type == "ai" else m for m in state["messages"]]
    main.build(first, [], "").update_state({"configurable": {"thread_id": "synthetic-thread"}}, {"messages": messages})
    with db_session.session_scope(engine) as session:
        task = session.get(ChatTask, "synthetic-task")
        task.state, task.cancel_requested, task.result = "cancelled", True, {"answer": ""}
    second = Model(messages=iter([AIMessage(content="新的合成答复")]))
    state, _, _, error, _ = run(second, "我停止了上一条，重新开始")
    assert error is None and state["messages"][-1].content == "新的合成答复"
    assert REPORT not in str([message.content for message in second.seen[0]])
    # 过去检查点仍可审计；修改后只改变后续上下文，不抹掉已有检查点版本。
    assert any(REPORT in str(row.checkpoint["channel_values"].get("messages", []))
               for row in saver.list({"configurable": {"thread_id": "synthetic-thread"}}))


@pytest.mark.parametrize("stop", [True, False])
def test_real_task_chat_report_and_next_request_respect_formal_delivery(queue, monkeypatch, stop):
    engine, instance = queue
    saver = setup(monkeypatch, engine)
    from polaris import results

    monkeypatch.setattr(results, "presentation", lambda *a: [])
    monkeypatch.setattr(results, "unverified_citations", lambda *a: set())
    first = Model(messages=iter([AIMessage(content="", tool_calls=[{
        "id": "task-report", "name": "career_report", "args": {}}])]), disable_streaming=True)
    chat = main.chat

    def complete(*args, **kwargs):
        turn = chat(*args, **kwargs, model=first, memory_first=True,
                    remember_fn=lambda *a, **k: {"events": [], "questions": []})
        if stop:
            with db_session.session_scope(engine) as session:
                session.get(ChatTask, "synthetic-task").cancel_requested = True
        return turn

    monkeypatch.setattr(main, "chat", complete)
    instance._work("synthetic-task")
    with db_session.session_scope(engine) as session:
        task = session.get(ChatTask, "synthetic-task")
        assert task.state == ("cancelled" if stop else "completed")
        assert task.result["answer"] == ("" if stop else REPORT)
    messages = saver.get_tuple({"configurable": {"thread_id": "synthetic-thread"}}).checkpoint["channel_values"]["messages"]
    assert all(message.additional_kwargs[TASK_ID] == "synthetic-task" for message in messages if message.type == "ai")
    assert messages[-1].content == REPORT  # 停止发生于图完成后，确实需要下一轮交付核实。
    second = Model(messages=iter([AIMessage(content="第二条合成回答")]))
    _, _, _, error, _ = run(second, "继续下一条")
    assert error is None
    assert (REPORT in str([message.content for message in second.seen[0]])) is (not stop)


def test_report_stopped_after_tool_result_does_not_reach_next_model_as_tool_content(queue, monkeypatch):
    engine, _ = queue
    saver = setup(monkeypatch, engine)
    stopped = []

    def finish(args):
        stopped.append(True)
        return FinalResponse(REPORT, "ready")

    monkeypatch.setattr(report_workflow, "report_tool", lambda *a: ToolSpec(
        "career_report", "合成报告", Args, finish, return_direct=True))
    first = Model(messages=iter([AIMessage(content="", tool_calls=[{
        "id": "tool-report", "name": "career_report", "args": {}}])]))
    state, _, _, error, _ = run(first, "报告", should_stop=lambda: bool(stopped), delivery_task_id="synthetic-task")
    assert state is None and error == main.STOPPED
    old = saver.get_tuple({"configurable": {"thread_id": "synthetic-thread"}}).checkpoint["channel_values"]["messages"]
    assert old[-1].type == "tool" and old[-1].content == REPORT
    with db_session.session_scope(engine) as session:
        session.get(ChatTask, "synthetic-task").state = "cancelled"
    second = Model(messages=iter([AIMessage(content="新回答")]))
    _, _, _, error, _ = run(second, "下一条")
    assert error is None and REPORT not in str([message.content for message in second.seen[0]])
    assert next(message for message in second.seen[0] if message.type == "tool").tool_call_id == "tool-report"


@pytest.mark.parametrize("fault", ["missing", "other_thread", "running", "database"])
def test_unknown_delivery_stops_before_next_answer_model(queue, monkeypatch, fault):
    engine, _ = queue
    setup(monkeypatch, engine)
    graph = main.build(Model(messages=iter([])), [], "")
    graph.update_state({"configurable": {"thread_id": "synthetic-thread"}}, {"messages": [
        HumanMessage(content="旧合成请求"), AIMessage(content=REPORT, additional_kwargs={TASK_ID: "synthetic-task"})]})
    with db_session.session_scope(engine) as session:
        task = session.get(ChatTask, "synthetic-task")
        if fault == "missing":
            session.delete(task)
        elif fault == "other_thread":
            task.thread_id, task.state = "another-thread", "cancelled"
        elif fault != "running":
            task.state = "cancelled"
    if fault == "database":
        def fail():
            raise RuntimeError("sk-do-not-expose-synthetic-secret")
        monkeypatch.setattr(task_delivery, "session_scope", fail)
    model = Model(messages=iter([AIMessage(content="不应该调用")]))
    state, _, _, error, _ = run(model, "下一条")
    assert state is None and "无法确认先前任务的交付状态" in error
    assert "sk-do-not-expose" not in error and model.seen == []


def test_redaction_precedes_actual_summarization_model(queue, monkeypatch):
    engine, _ = queue
    setup(monkeypatch, engine)
    graph = main.build(Model(messages=iter([])), [], "")
    graph.update_state({"configurable": {"thread_id": "synthetic-thread"}}, {"messages": [
        HumanMessage(content="旧合成请求"), AIMessage(content=REPORT, additional_kwargs={TASK_ID: "synthetic-task"})]})
    with db_session.session_scope(engine) as session:
        session.get(ChatTask, "synthetic-task").state = "failed"
    monkeypatch.setattr(main, "SUMMARY_TRIGGER_TOKENS", 1)
    monkeypatch.setattr(main, "SUMMARY_KEEP", ("messages", 1))
    model = Model(messages=iter([AIMessage(content="只保留合成用户请求和未交付状态"), AIMessage(content="新的合成答案")]))
    state, _, _, error, _ = run(model, "新请求")
    assert error is None and state["messages"][-1].content == "新的合成答案"
    assert len(model.seen) == 2 and REPORT not in str([message.content for call in model.seen for message in call])


def test_redaction_keeps_user_and_actual_tool_evidence_and_pairing(queue, monkeypatch):
    engine, _ = queue
    setup(monkeypatch, engine)
    with db_session.session_scope(engine) as session:
        session.get(ChatTask, "synthetic-task").state = "cancelled"
    messages = [HumanMessage(content="合成用户原话", id="human"), AIMessage(
        content="隐藏中间文字", id="call", additional_kwargs={TASK_ID: "synthetic-task"},
        tool_calls=[{"id": "known-tool", "name": "synthetic-query", "args": {}}]),
        ToolMessage(content="实际工具返回的合成证据", id="tool", name="synthetic-query", tool_call_id="known-tool"),
        AIMessage(content=REPORT, id="answer", additional_kwargs={TASK_ID: "synthetic-task"})]
    update = task_delivery.TaskDelivery(None, "synthetic-thread").before_agent({"messages": messages}, None)
    changed = {message.id: message for message in update["messages"]}
    assert set(changed) == {"call", "answer"}
    assert changed["call"].tool_calls == messages[1].tool_calls and changed["call"].content == ""
    assert changed["answer"].content == task_delivery.UNDELIVERED
    current = [changed.get(message.id, message) for message in messages]
    assert task_delivery.TaskDelivery(None, "synthetic-thread").before_agent({"messages": current}, None) is None


def test_legacy_import_does_not_publish_queued_model_output_from_older_checkpoints(queue, monkeypatch):
    engine, _ = queue
    setup(monkeypatch, engine)
    graph = main.build(Model(messages=iter([])), [], "")
    config = {"configurable": {"thread_id": "synthetic-thread"}}
    graph.update_state(config, {"messages": [HumanMessage(content="真实应显示的合成请求", id="user"),
                                             AIMessage(content=REPORT, id="hidden", additional_kwargs={TASK_ID: "synthetic-task"})]})
    graph.update_state(config, {"messages": [AIMessage(content=task_delivery.UNDELIVERED, id="hidden",
                                                      additional_kwargs={TASK_ID: "synthetic-task", "polaris_internal": True})]})
    with db_session.session_scope(engine) as session:
        thread = session.get(ChatThread, "synthetic-thread")
        thread.history_imported = False
        transcript.ensure_thread(session, "synthetic-thread")
        assert [row.content for row in session.scalars(select(ChatMessage))] == ["真实应显示的合成请求"]


def test_task_tagging_preserves_actual_human_approval_and_explicit_rejection(queue, monkeypatch):
    from langgraph.types import Command

    engine, _ = queue
    saver = setup(monkeypatch, engine)
    effects = []

    class ForgetArgs(BaseModel):
        attribute: str

    monkeypatch.setitem(main.REGISTRY, "memory_forget", ToolSpec(
        "memory_forget", "合成审批动作", ForgetArgs, lambda args: effects.append(args.attribute), read_only=False))
    first = Model(messages=iter([AIMessage(content="", tool_calls=[{
        "id": "approval", "name": "memory_forget", "args": {"attribute": "school"}}])]))
    state, _, _, error, _ = run(first, "合成审批请求", delivery_task_id="synthetic-task")
    assert error is None and main._interrupts(state) and effects == []
    with db_session.session_scope(engine) as session:
        session.get(ChatTask, "synthetic-task").state = "waiting_approval"
    second = Model(messages=iter([AIMessage(content="已拒绝合成动作")]))
    state, _, _, error, _ = main._run(Command(resume={"decisions": [{"type": "reject", "message": "本人拒绝"}]}),
                                    "synthetic-thread", NOW, [], second, None, "synthetic-resume",
                                    delivery_task_id="synthetic-resume-task")
    assert error is None and not main._interrupts(state) and effects == []
    assert state["messages"][-1].content == "已拒绝合成动作"
    assert state["messages"][-1].additional_kwargs[TASK_ID] == "synthetic-resume-task"
    assert saver.get_tuple({"configurable": {"thread_id": "synthetic-thread"}}) is not None


def test_task_owner_is_saved_even_when_stop_occurs_at_model_node_update(queue, monkeypatch):
    engine, _ = queue
    saver = setup(monkeypatch, engine)
    stopped, generated = [], []
    monkeypatch.setattr(report_workflow, "report_tool", lambda *a: ToolSpec(
        "career_report", "合成报告", Args, lambda args: generated.append(True), return_direct=True))
    first = Model(messages=iter([AIMessage(content=REPORT, tool_calls=[{
        "id": "not-started", "name": "career_report", "args": {}}])]), disable_streaming=True)

    def event(row):
        if row["type"] == "tool":
            stopped.append(True)

    state, _, _, error, _ = run(first, "合成任务", delivery_task_id="synthetic-task", on_event=event,
                               should_stop=lambda: bool(stopped))
    assert error == main.STOPPED and state is None and generated == []
    messages = saver.get_tuple({"configurable": {"thread_id": "synthetic-thread"}}).checkpoint["channel_values"]["messages"]
    assert next(message for message in messages if message.type == "ai").additional_kwargs[TASK_ID] == "synthetic-task"
    with db_session.session_scope(engine) as session:
        session.get(ChatTask, "synthetic-task").state = "cancelled"
    second = Model(messages=iter([AIMessage(content="新回答")]))
    _, _, _, error, _ = run(second, "重新开始")
    assert error is None and REPORT not in str([message.content for message in second.seen[0]])


def test_regular_answer_has_owner_and_completed_alone_does_not_prove_delivery(queue, monkeypatch):
    engine, _ = queue
    setup(monkeypatch, engine)
    first = Model(messages=iter([AIMessage(content=REPORT)]))
    state, _, _, error, _ = run(first, "不调用工具的合成回答", delivery_task_id="synthetic-task")
    assert error is None and state["messages"][-1].additional_kwargs[TASK_ID] == "synthetic-task"
    with db_session.session_scope(engine) as session:
        task = session.get(ChatTask, "synthetic-task")
        task.state, task.result = "completed", {"answer": REPORT}  # 无正式ChatMessage，不当作已交付。
    second = Model(messages=iter([AIMessage(content="新回答")]))
    _, _, _, error, _ = run(second, "下一条")
    assert error is None and REPORT not in str([message.content for message in second.seen[0]])


def test_stop_at_actual_approval_interrupt_keeps_pending_decision_in_queue_snapshot_data(queue, monkeypatch):
    engine, instance = queue
    saver = setup(monkeypatch, engine)
    effects = []

    class ForgetArgs(BaseModel):
        attribute: str

    monkeypatch.setitem(main.REGISTRY, "memory_forget", ToolSpec(
        "memory_forget", "合成审批动作", ForgetArgs, lambda args: effects.append(args.attribute), read_only=False))
    model = Model(messages=iter([AIMessage(content="", tool_calls=[{
        "id": "stopped-approval", "name": "memory_forget", "args": {"attribute": "school"}}])]), disable_streaming=True)
    chat = main.chat
    monkeypatch.setattr(main, "chat", lambda *a, **kw: chat(*a, **kw, model=model, memory_first=True,
                        remember_fn=lambda *a, **kw: {"events": [], "questions": []}))

    def stop_after_interrupt():
        row = saver.get_tuple({"configurable": {"thread_id": "synthetic-thread"}})
        interrupted = bool(row and any(channel == "__interrupt__" for _, channel, _ in row.pending_writes))
        if interrupted:
            with db_session.session_scope(engine) as session:
                session.get(ChatTask, "synthetic-task").cancel_requested = True
        return interrupted

    monkeypatch.setattr(instance, "_stopper", lambda task_id: stop_after_interrupt)
    instance._work("synthetic-task")
    with db_session.session_scope(engine) as session:
        task = session.get(ChatTask, "synthetic-task")
        thread = session.get(ChatThread, "synthetic-thread")
        assert task.state == "waiting_approval" and task.error == main.STOPPED and task.cancel_requested
        assert task.result["answer"] == "" and task.result["approvals"] == thread.approvals
        assert thread.approvals[0]["name"] == "memory_forget" and effects == []
        assert session.scalars(select(ChatMessage)).all() == []
