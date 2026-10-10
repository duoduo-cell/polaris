"""最小 agent：用按剧本回答的假模型跑通"调工具 → 回答"，检查记账和完整回放。不调任何真实服务。"""
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from polaris.agent.minimal import ask
from polaris.ingest import RawItemIn, ingest_raw_items
from polaris.replay import Cassette


class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self


def script(item_id: str) -> ScriptedModel:
    return ScriptedModel(messages=iter([
        AIMessage(content="", tool_calls=[{"name": "raw_items_search", "args": {"query": "穹宇集团"}, "id": "c1"}]),
        AIMessage(content="", tool_calls=[{"name": "raw_items_get", "args": {"item_id": item_id}, "id": "c2"}]),
        AIMessage(content=f"日薪 300-400 元（{item_id}）"),
    ]))


def test_agent_calls_tools_and_everything_is_traced(db, tmp_path):
    rid = f"R-{ingest_raw_items([RawItemIn(source='manual', raw_text='穹宇集团 Agent 实习，日薪 300-400 元')]).ids[0]}"
    cassette = Cassette(tmp_path / "run.jsonl", "record")
    run = ask("穹宇集团的实习日薪多少？", model=script(rid), cassette=cassette)
    cassette.save()
    assert run.error is None and run.answer == f"日薪 300-400 元（{rid}）"
    assert [c["tool"] for c in run.tool_calls] == ["raw_items_search", "raw_items_get"]
    assert all(c["ok"] for c in run.tool_calls)
    assert len(run.llm_call_ids) == 3 and all(run.llm_call_ids)  # 三次模型调用都记了账

    replayed = ask("穹宇集团的实习日薪多少？", cassette=Cassette(tmp_path / "run.jsonl", "replay", llm_replay=True),
                   record_db=False)
    assert replayed.answer == run.answer
    assert all(c["replayed"] for c in replayed.tool_calls)


def test_agent_recovers_from_injected_fault(db):
    rid = f"R-{ingest_raw_items([RawItemIn(source='manual', raw_text='穹宇集团 Agent 实习')]).ids[0]}"
    model = ScriptedModel(messages=iter([
        AIMessage(content="", tool_calls=[{"name": "raw_items_search", "args": {"query": "穹宇集团"}, "id": "c1"}]),
        AIMessage(content="", tool_calls=[{"name": "raw_items_search", "args": {"query": "穹宇集团"}, "id": "c2"}]),
        AIMessage(content=f"找到了 {rid}"),
    ]))
    run = ask("穹宇集团有什么实习？", model=model, faults={"raw_items_search": ["rate_limit"]})
    assert [c["error_kind"] for c in run.tool_calls] == ["injected", None]
    assert rid in run.answer
