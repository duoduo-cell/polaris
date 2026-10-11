"""记忆更新规则（M5）：直接喂候选事实，不调模型。每种决定一个用例，外加双时间线的两种查询。"""
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from polaris.db.models import MemoryEvent, MemoryFact
from polaris.db.session import session_scope
from polaris.memory.update import Candidate, Context, apply, card, confirm, forget, known_at, pending, render_card

T0 = datetime(2026, 10, 1, 10, tzinfo=UTC)


def say(c: Candidate, at: datetime = T0, text: str = "测试") -> tuple[dict, str | None]:
    with session_scope() as s:
        return apply(Context(s, at, text), c)


def C(attribute: str, value: str, **kw) -> Candidate:
    return Candidate(attribute=attribute, value=value, statement=kw.pop("statement", f"用户{attribute}={value}"), **kw)


def values_on(on: date, attribute: str) -> list[str]:
    with session_scope() as s:
        return [f.value for f in card(s, on) if f.attribute == attribute]


def test_add_noop_and_multi_values(db):
    assert say(C("target_city", "北京"))[0]["op"] == "add"
    assert say(C("target_city", "北京市"), T0 + timedelta(days=2))[0]["op"] == "noop"  # 归一后一样
    assert say(C("target_city", "上海"))[0]["op"] == "add"  # 多值属性，不顶掉北京
    assert values_on(T0.date(), "target_city") == ["北京", "上海"]
    with session_scope() as s:
        bj = s.execute(select(MemoryFact).where(MemoryFact.value == "北京")).scalar_one()
        assert bj.last_confirmed_at == T0 + timedelta(days=2)


def test_recent_contradiction_asks_instead_of_overwriting(db):
    say(C("days_per_week", "4", statement="用户每周能到岗 4 天"))
    e, q = say(C("days_per_week", "3", statement="用户每周能到岗 3 天"), T0 + timedelta(days=3))
    assert e["op"] == "ask" and "4 天" in q and "3 天" in q
    assert values_on(T0.date() + timedelta(days=3), "days_per_week") == ["4"]  # 没问清之前不改
    with session_scope() as s:
        confirm(s, e["fact_id"], accept=True, now=T0 + timedelta(days=3, hours=1))
    assert values_on(T0.date() + timedelta(days=4), "days_per_week") == ["3"]


def test_change_signal_updates_and_history_is_kept(db):
    say(C("days_per_week", "4"))
    e, _ = say(C("days_per_week", "3", change_signal=True), T0 + timedelta(days=5))
    assert e["op"] == "update"
    assert values_on(T0.date() + timedelta(days=6), "days_per_week") == ["3"]
    with session_scope() as s:  # 第 2 天时记着的还是 4
        assert [f.value for f in known_at(s, T0 + timedelta(days=2)) if f.attribute == "days_per_week"] == ["4"]
        old = s.execute(select(MemoryFact).where(MemoryFact.value == "4")).scalar_one()
        assert old.status == "superseded" and old.superseded_at == T0 + timedelta(days=5)


def test_stale_statement_is_replaced_without_asking(db):
    say(C("grad_year", "2027"))
    assert say(C("grad_year", "2028"), T0 + timedelta(days=40))[0]["op"] == "update"


def test_future_change_is_scheduled(db):
    say(C("days_per_week", "4"))
    e, _ = say(C("days_per_week", "3", valid_from=date(2026, 11, 1)), T0 + timedelta(days=1))
    assert e["op"] == "schedule"
    assert values_on(date(2026, 10, 20), "days_per_week") == ["4"]
    assert values_on(date(2026, 11, 5), "days_per_week") == ["3"]


def test_inference_waits_for_confirmation_and_rejection_is_remembered(db):
    e, q = say(C("applied", "Agent 开发实习", qualifier="字节", kind="inferred", statement="用户投递过字节的 Agent 开发实习"))
    assert e["op"] == "pending" and "对吗" in q
    assert values_on(T0.date(), "applied") == []  # 待确认的不进卡片
    with session_scope() as s:
        assert [f.id for f in pending(s)] == [e["fact_id"]]
        confirm(s, e["fact_id"], accept=False)
    again, q2 = say(C("applied", "Agent开发实习", qualifier="字节跳动", kind="inferred"), T0 + timedelta(days=1))
    assert again["op"] == "suppressed" and q2 is None


def test_negation_invalidates(db):
    say(C("company_like", "字节"))
    e, _ = say(C("company_like", "ByteDance", polarity="negate"), T0 + timedelta(days=1))
    assert e["op"] == "invalidate"
    assert values_on(T0.date() + timedelta(days=2), "company_like") == []
    assert say(C("company_like", "腾讯", polarity="negate"))[0]["op"] == "noop"  # 本来就没有


def test_forget_retracts_and_suppresses(db):
    say(C("school", "某大学"))
    with session_scope() as s:
        out = forget(s, "school", now=T0 + timedelta(days=1))
    assert [x["op"] for x in out] == ["forget"] and values_on(T0.date() + timedelta(days=2), "school") == []
    assert say(C("school", "某大学", kind="inferred"), T0 + timedelta(days=3))[0]["op"] == "suppressed"


def test_qualified_slots_are_independent(db):
    say(C("interview_stage", "一面", qualifier="字节"))
    assert say(C("interview_stage", "一面", qualifier="腾讯"))[0]["op"] == "add"
    e, _ = say(C("interview_stage", "二面", qualifier="字节跳动", change_signal=True), T0 + timedelta(days=2))
    assert e["op"] == "update" and e["qualifier"] == "字节跳动"
    with session_scope() as s:
        text = render_card(card(s, T0.date() + timedelta(days=3)))
        assert "面试进度（字节跳动）：二面" in text and "面试进度（腾讯）：一面" in text
        assert s.execute(select(MemoryEvent.op).order_by(MemoryEvent.id)).scalars().all() == ["add", "add", "update"]


def test_unknown_attribute_is_skipped(db):
    with session_scope() as s:
        e, q = apply(Context(s, T0, "x"), Candidate(attribute="favorite_food", value="面", statement="用户爱吃面"))
    assert e["op"] == "skip" and q is None
    with pytest.raises(ValueError):
        with session_scope() as s:
            confirm(s, 999, accept=True)


def test_answering_the_question_in_words_confirms_or_rejects(db):
    say(C("days_per_week", "4"))
    e, _ = say(C("days_per_week", "3"), T0 + timedelta(days=1))
    assert e["op"] == "ask"
    yes, _ = say(C("days_per_week", "3", statement="对，用户每周 3 天"), T0 + timedelta(days=1, minutes=5))
    assert yes["op"] == "confirm" and values_on(T0.date() + timedelta(days=2), "days_per_week") == ["3"]
    e2, _ = say(C("skill", "LangGraph", kind="inferred"), T0 + timedelta(days=2))
    no, _ = say(C("skill", "LangGraph", polarity="negate"), T0 + timedelta(days=2, minutes=1))
    assert e2["op"] == "pending" and no["op"] == "reject"
    assert say(C("skill", "LangGraph", kind="inferred"), T0 + timedelta(days=3))[0]["op"] == "suppressed"


def test_memory_tools_show_card_history_and_forget(db):
    from polaris.tools import REGISTRY
    from polaris.tools.runtime import ToolRuntime

    say(C("days_per_week", "4", statement="用户每周 4 天"), text="我每周能去 4 天")
    say(C("days_per_week", "3", change_signal=True), T0 + timedelta(days=5), text="改成每周 3 天了")
    rt = ToolRuntime()
    assert '"value": "3"' in rt.invoke(REGISTRY["memory_card"], {"on": "2026-10-07"})
    hist = rt.invoke(REGISTRY["memory_history"], {"attribute": "days_per_week"})
    assert hist.index("我每周能去 4 天") < hist.index("改成每周 3 天了") and '"superseded": "2026-10-06"' in hist
    assert '"value": "4"' in rt.invoke(REGISTRY["memory_known_at"], {"day": "2026-10-03"})
    assert "没有记过" in rt.invoke(REGISTRY["memory_history"], {"attribute": "salary_expect"})
    assert '"forgotten": 1' in rt.invoke(REGISTRY["memory_forget"], {"attribute": "days_per_week"})
    assert rt.invoke(REGISTRY["memory_forget"], {"attribute": "school"}).startswith("出错了")


def test_interview_progress_updates_and_specific_reply_confirms_inference(db):
    say(C("interview_stage", "一面", qualifier="字节"))
    assert say(C("interview_stage", "二面", qualifier="字节"), T0 + timedelta(days=2))[0]["op"] == "update"  # 不追问
    e, _ = say(C("applied", "字节跳动", qualifier="字节", kind="inferred"), T0 + timedelta(days=3))
    ok, _ = say(C("applied", "Agent 开发实习", qualifier="字节跳动", statement="用户投的是字节的 Agent 开发实习"),
                T0 + timedelta(days=3, minutes=2))
    assert e["op"] == "pending" and ok["op"] == "confirm" and ok["fact_id"] == e["fact_id"]
    with session_scope() as s:
        assert s.get(MemoryFact, e["fact_id"]).value == "Agent 开发实习"


def test_memory_eval_scoring_handles_optional_and_fuzzy_values():
    from polaris.eval.memory_eval import score_turn

    golds = [{"op": "add", "attr": "school", "value": ["浙大", "浙江大学"]},
             {"op": "add", "attr": "major", "value": ["计算机"], "optional": True},
             {"op": "pending", "attr": "applied", "q": "字节", "value": [""]}]
    preds = [{"op": "add", "attribute": "school", "qualifier": None, "value": "浙江大学"},
             {"op": "pending", "attribute": "applied", "qualifier": "字节跳动", "value": "字节跳动"},
             {"op": "noop", "attribute": "grad_year", "qualifier": None, "value": "2027"}]
    r = score_turn(preds, golds)
    assert r["exact"] and len(r["tp"]) == 2  # 可选的没做不算漏，noop 不计
    r = score_turn(preds + [{"op": "add", "attribute": "major", "qualifier": None, "value": "计算机科学"},
                            {"op": "add", "attribute": "skill", "qualifier": None, "value": "rag"}], golds)
    assert not r["exact"] and [p["attribute"] for p in r["fp"]] == ["skill"]  # 可选的做了不算多


def test_denying_a_company_clears_linked_inferences_and_suppresses_by_company(db):
    e, _ = say(C("interview_stage", "准备面试中", qualifier="腾讯", kind="inferred"))
    no, _ = say(C("applied", "投递过", qualifier="腾讯", polarity="negate", statement="用户没投腾讯"), T0 + timedelta(minutes=1))
    with session_scope() as s:
        assert s.get(MemoryFact, e["fact_id"]).status == "retracted"  # 牵连的面试进度推断一并作废
    later = say(C("interview_stage", "在准备腾讯面试", qualifier="腾讯", kind="inferred"), T0 + timedelta(days=5))[0]
    assert later["op"] == "suppressed"  # 说法换了，按公司仍然抑制
    assert say(C("interview_stage", "一面", qualifier="腾讯"), T0 + timedelta(days=6))[0]["op"] == "add"  # 你亲口说的照记


def test_progress_with_a_future_date_still_takes_effect_now(db):
    say(C("interview_stage", "一面", qualifier="字节"))
    e, _ = say(C("interview_stage", "一面已过，等二面", qualifier="字节", valid_from=date(2026, 10, 13)), T0 + timedelta(days=4))
    assert e["op"] == "update" and values_on(T0.date() + timedelta(days=4), "interview_stage") == ["一面已过，等二面"]


def test_restating_the_old_value_rejects_the_pending_one(db):
    say(C("days_per_week", "5"))
    e, _ = say(C("days_per_week", "4"), T0 + timedelta(days=1))
    assert e["op"] == "ask"
    back, _ = say(C("days_per_week", "5", change_signal=True), T0 + timedelta(days=1, minutes=2))
    assert back["op"] == "reject" and values_on(T0.date() + timedelta(days=2), "days_per_week") == ["5"]
    with session_scope() as s:
        assert pending(s) == []


def test_change_signal_needs_explicit_words():
    from polaris.memory.extract import has_change_words

    assert not has_change_words("我每周能去 5 天，能实习 6 个月。")
    assert has_change_words("现在每周只能去 3 天了") and has_change_words("还是 5 天吧，刚才说错了")


def test_same_inference_is_not_asked_twice(db):
    e, _ = say(C("applied", "投递过", qualifier="腾讯", kind="inferred"))
    again, q = say(C("applied", "投递过", qualifier="腾讯", kind="inferred"), T0 + timedelta(days=1))
    assert e["op"] == "pending" and again["op"] == "noop" and q is None
