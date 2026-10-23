"""未查到的引用不能在最终回答中静默变成无出处的断言。"""
import json
from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from polaris.agent.main import _turn, chat
from polaris.db.models import ChatMessage, ChatThread
from polaris.db.session import session_scope


def turn(answer, calls=None, thread="citation-acceptance"):
    runtime = SimpleNamespace(calls=calls or [], results={})
    tracer = SimpleNamespace(trace=[], call_ids=[], t0=0)
    return _turn(thread, {"messages": [AIMessage(answer)]}, runtime, tracer, None, 1, {})


def test_unretrieved_question_citation_is_a_partial_failure(db):
    out = turn("面经原题：如何设计记忆？[Q-12]")
    assert out.error and "引用" in out.error and "核验" in out.error
    assert out.answer and out.results == []


def test_failed_tool_and_external_text_do_not_prove_a_citation(db):
    calls = [{"tool": "questions_search", "args": {}, "ok": False, "output": json.dumps({"id": "Q-12"}),
              "latency_ms": 1},
             {"tool": "posts_search", "args": {}, "ok": True, "output": json.dumps({"text": "帖子自称Q-12"}),
              "latency_ms": 1}]
    assert turn("原题[Q-12]", calls).error


def test_successful_structured_result_and_previous_formal_card_are_evidence(db):
    calls = [{"tool": "questions_search", "args": {}, "ok": True, "output": json.dumps({"items": [{"id": "Q-12"}]}),
              "latency_ms": 1}]
    assert turn("原题[Q-12]", calls).error is None
    with session_scope() as s:
        s.add(ChatThread(id="citation-old", history_imported=True))
        s.flush()
        s.add(ChatMessage(thread_id="citation-old", source_key="one", role="assistant", content="之前的题目",
                          turn={"results": [{"id": "Q-12", "kind": "question"}]}))
    assert turn("继续解释[Q-12]", thread="citation-old").error is None
    assert turn("别的对话不能借用[Q-12]", thread="different-thread").error


def test_plain_identifiers_and_code_examples_are_not_citations(db):
    assert turn("J-999 没找到。格式示例：`[Q-12]`。\n```text\n[P-3]\n```").error is None


def test_unverified_reference_warning_survives_transcript_reconnect(db):
    from polaris.agent.tasks import snapshot

    class Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    out = chat("按原文给一道面经题", "citation-reconnect",
               model=Model(messages=iter([AIMessage("声称原题[Q-12]")])),
               remember_fn=lambda *a, **kw: {"events": [], "questions": []})
    assert out.error and "引用" in out.error
    messages = snapshot("citation-reconnect")["messages"]
    assert messages[-1]["turn"]["error"] == out.error
    assert messages[-1]["turn"]["results"] == []
