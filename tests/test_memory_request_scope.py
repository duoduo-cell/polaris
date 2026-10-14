"""单次查询不能新增、作废或确认长期偏好；同句真实事实仍正常写入。"""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from polaris.db.models import MemoryEvent, MemoryFact
from polaris.db.session import session_scope
from polaris.memory.extract import Extraction, FactCandidate, remember
from polaris.memory.update import Candidate, Context, apply

NOW = datetime(2026, 10, 12, tzinfo=UTC)


class Model:
    def __init__(self, *facts):
        self.facts = facts

    def extract(self, *a, **kw):
        return SimpleNamespace(ok=True, value=Extraction(facts=list(self.facts)))


def fact(attribute, value, evidence="", **kw):
    return FactCandidate(attribute=attribute, value=value, statement=f"用户{value}", kind="stated", evidence=evidence, **kw)


@pytest.mark.parametrize("scope", ["request", "profile"])
def test_request_cannot_write_preference_even_when_model_mislabels_it(db, scope):
    message = "这次只查北京的字节实习岗位。"
    model = Model(fact("target_city", "北京", message, scope=scope),
                  fact("company_like", "字节跳动", message, scope=scope))
    result = remember(message, llm=model, now=NOW, thread_id="scope-test")
    assert result == {"events": [], "questions": []}
    with session_scope() as s:
        assert not s.scalars(select(MemoryFact)).all()
        audits = s.scalars(select(MemoryEvent)).all()
        assert len(audits) == 2 and all(e.op == "request_only" and e.thread_id == "scope-test" for e in audits)


def test_mixed_message_keeps_skill_and_long_term_city_not_request_city(db):
    message = "我会Python，我以后优先杭州，今天先查北京腾讯的实习岗位。"
    result = remember(message, llm=Model(fact("skill", "Python", "我会Python"),
                      fact("target_city", "杭州", "我以后优先杭州"),
                      fact("target_city", "北京", "今天先查北京腾讯的实习岗位")), now=NOW)
    assert len(result["events"]) == 2 and not result["questions"]
    with session_scope() as s:
        assert {(f.attribute, f.value) for f in s.scalars(select(MemoryFact))} == {("skill", "Python"), ("target_city", "杭州")}


def test_request_does_not_invalidate_existing_preference(db):
    with session_scope() as s:
        apply(Context(s, NOW, "我长期想去上海"), Candidate("target_city", "上海", "用户想去上海"))
    message = "这次查北京的实习岗位，不看上海。"
    remember(message, llm=Model(fact("target_city", "上海", message, scope="request", polarity="negate")), now=NOW)
    with session_scope() as s:
        assert s.scalar(select(MemoryFact)).status == "active"


def test_unverifiable_query_evidence_is_not_written_as_preference(db):
    result = remember("帮我找北京的实习岗位", llm=Model(fact("company_like", "字节跳动", "用户喜欢字节")), now=NOW)
    assert not result["events"] and not result["questions"]
    with session_scope() as s:
        assert not s.scalars(select(MemoryFact)).all()


def test_explicit_long_term_preference_still_writes(db):
    message = "以后找实习只考虑北京，记住这个偏好。"
    result = remember(message, llm=Model(fact("target_city", "北京", message)), now=NOW)
    assert result["events"][0]["op"] == "add"


def test_request_audit_and_memory_write_share_callers_transaction(db):
    message = "这次只查北京的实习岗位。"
    with session_scope() as s:
        remember(message, llm=Model(fact("target_city", "北京", message, scope="request")), session=s, now=NOW)
        s.flush()
        assert s.scalar(select(MemoryEvent)).op == "request_only"
        s.rollback()
    with session_scope() as s:
        assert not s.scalars(select(MemoryEvent)).all()
