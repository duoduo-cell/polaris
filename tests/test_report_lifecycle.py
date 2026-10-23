"""实际LangGraph与内存checkpointer的收尾故障；不启动服务／数据库或调用真实模型。"""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from polaris.agent import main, report_workflow
from polaris.llm.recorder import MemoryRecorder
from polaris.tools.base import FinalResponse, ToolSpec
from polaris.tools.runtime import ToolRuntime


class Model(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class Args(BaseModel):
    pass


def setup_run(monkeypatch, fault=None, stopped=None):
    saver = InMemorySaver()
    recorder = MemoryRecorder()
    monkeypatch.setattr(main, "checkpointer", lambda: saver)
    monkeypatch.setattr(main, "DbRecorder", lambda: recorder)
    monkeypatch.setattr(main, "_system_prompt", lambda *a: "合成报告收尾测试")
    monkeypatch.setattr(main, "ToolRuntime", lambda **kw: ToolRuntime(**kw, record_db=False))
    final = FinalResponse("合成报告正文", "ready", llm_call_ids=[999], model_trace=[{
        "kind": "answer", "model": "synthetic-preparation", "ms": 1, "input_tokens": 2, "output_tokens": 3,
        "cost_usd": None}])

    def finish(args):
        if stopped is not None:
            stopped.append(True)
        return final

    monkeypatch.setattr(report_workflow, "report_tool", lambda *a: ToolSpec(
        "career_report", "合成收尾", Args, finish, return_direct=True))
    original_build = main.build

    def build(*a, **kw):
        graph = original_build(*a, **kw)

        def fail(*args, **kwargs):
            raise RuntimeError("synthetic persistence error sk-must-not-leak")

        return SimpleNamespace(invoke=graph.invoke,
                               update_state=fail if fault == "update" else graph.update_state,
                               get_state=fail if fault == "read" else graph.get_state)

    monkeypatch.setattr(main, "build", build)
    model = Model(messages=iter([AIMessage(content="", tool_calls=[{
        "id": "report-final", "name": "career_report", "args": {}}])]))
    return saver, recorder, final, model


def invoke(model, stopped=None):
    return main._run({"messages": [HumanMessage(content="合成完整报告")]}, "synthetic-report-lifecycle",
                     datetime(2026, 10, 23, tzinfo=UTC), [], model, None, "synthetic-test",
                     should_stop=(lambda: bool(stopped)) if stopped is not None else None)


def test_report_final_message_and_usage_are_saved_by_actual_graph(monkeypatch):
    saver, recorder, final, model = setup_run(monkeypatch)
    state, runtime, tracer, error, _ = invoke(model)
    assert error is None and state["messages"][-1].type == "ai"
    assert state["messages"][-1].content == final.answer
    checkpoint = saver.get_tuple({"configurable": {"thread_id": "synthetic-report-lifecycle"}})
    assert checkpoint.checkpoint["channel_values"]["messages"][-1].content == final.answer
    assert runtime.calls[-1]["ok"] is True
    assert len(recorder.rows) == 1 and tracer.call_ids == [1, 999]
    assert tracer.trace[-1]["cost_usd"] is None


@pytest.mark.parametrize("fault", ["update", "read"])
def test_generated_report_is_not_claimed_saved_when_final_checkpoint_is_unconfirmed(monkeypatch, fault):
    saver, _, _, model = setup_run(monkeypatch, fault=fault)
    state, runtime, tracer, error, _ = invoke(model)
    assert state is None and "最终会话保存未能确认" in error
    assert "sk-must-not-leak" not in error
    assert runtime.calls[-1]["ok"] is True  # 工具已生成；会话交付另有错误，不能算工具没有执行。
    assert tracer.call_ids == [1, 999]  # 保存失败不抹掉真实生成用量，不重跑模型。
    saved = saver.get_tuple({"configurable": {"thread_id": "synthetic-report-lifecycle"}}).checkpoint["channel_values"]["messages"]
    assert saved[-1].type == ("tool" if fault == "update" else "ai")  # 读回失败不能冒充写入肯定失败。


def test_stop_after_report_tool_never_appends_a_final_ai_answer(monkeypatch):
    stopped = []
    saver, recorder, _, model = setup_run(monkeypatch, stopped=stopped)
    state, _, tracer, error, _ = invoke(model, stopped)
    assert state is None and error == main.STOPPED
    checkpoint = saver.get_tuple({"configurable": {"thread_id": "synthetic-report-lifecycle"}})
    messages = checkpoint.checkpoint["channel_values"]["messages"]
    assert not any(m.type == "ai" and m.content == "合成报告正文" for m in messages)
    assert len(recorder.rows) == 1 and tracer.call_ids == [1, 999]
