"""北京时间午夜的业务日期；数据库审计时间仍保存真实 UTC 时刻。"""
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from polaris.db.models import MemoryFact
from polaris.db.session import session_scope
from polaris.memory.update import Candidate, Context, apply, card, confirm, forget

NIGHT = datetime(2026, 10, 22, 17, tzinfo=UTC)  # 北京 10-23 01:00
DAY = date(2026, 10, 23)
BEFORE = datetime(2026, 10, 22, 10, tzinfo=UTC)


def say(value, at=BEFORE, **kw):
    with session_scope() as s:
        return apply(Context(s, at, "合成日期测试"), Candidate("days_per_week", value, f"每周{value}天", **kw))


def test_midnight_change_is_current_not_tomorrow(db):
    say("4")
    event, _ = say("3", NIGHT, valid_from=DAY, change_signal=True)
    assert event["op"] == "update"
    with session_scope() as s:
        old = s.scalar(select(MemoryFact).where(MemoryFact.value == "4"))
        assert old.valid_to == DAY
        assert old.superseded_at == NIGHT


@pytest.mark.parametrize("operation", ["update", "invalidate", "forget", "confirm"])
def test_end_dates_follow_local_day_and_instants_stay_utc(db, operation):
    say("4")
    if operation == "confirm":
        event, _ = say("3")
        with session_scope() as s:
            confirm(s, event["fact_id"], True, now=NIGHT)
    elif operation == "forget":
        with session_scope() as s:
            forget(s, "days_per_week", now=NIGHT)
    else:
        say("3" if operation == "update" else "4", NIGHT,
            change_signal=True, polarity="negate" if operation == "invalidate" else "assert")
    with session_scope() as s:
        old = s.scalar(select(MemoryFact).where(MemoryFact.value == "4"))
        assert old.valid_to == DAY
        assert old.superseded_at == NIGHT


def test_system_context_and_extraction_share_local_today(db, monkeypatch):
    from polaris.agent.main import _system_prompt
    from polaris.memory import extract as module

    say("3", valid_from=DAY)
    prompt = _system_prompt(NIGHT, [])
    assert DAY.isoformat() in prompt and "每周能到岗几天：3" in prompt
    captured = {}

    def fake_extract(llm, message, context, today, *args, **kw):
        captured.update(context=context, today=today)
        return []

    monkeypatch.setattr(module, "extract", fake_extract)
    module.remember("合成测试，不写记忆", now=NIGHT, llm=object())
    assert captured["today"] == DAY and "每周能到岗几天：3" in captured["context"]


def test_default_card_and_tool_day_agree_but_explicit_date_is_kept(db, monkeypatch):
    from polaris.memory import update
    from polaris.tools import memory

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NIGHT.astimezone(tz) if tz else NIGHT.replace(tzinfo=None)

    monkeypatch.setattr(update, "datetime", Clock)
    monkeypatch.setattr(memory, "datetime", Clock)
    say("3", valid_from=DAY)
    with session_scope() as s:
        assert [f.value for f in card(s)] == ["3"]
    assert memory.memory_card(memory.MemoryCardArgs())["on"] == DAY.isoformat()
    explicit = memory.memory_card(memory.MemoryCardArgs(on=date(2026, 10, 22)))
    assert explicit["on"] == "2026-10-22" and explicit["facts"] == []


def test_known_at_day_ends_at_beijing_midnight(db):
    from polaris.tools.memory import MemoryHistoryArgs, MemoryKnownAtArgs, memory_history, memory_known_at

    say("4", datetime(2026, 10, 22, 15, tzinfo=UTC))
    say("3", NIGHT, change_signal=True)
    assert [f["value"] for f in memory_known_at(MemoryKnownAtArgs(day=date(2026, 10, 22)))["facts"]] == ["4"]
    history = memory_history(MemoryHistoryArgs(attribute="days_per_week"))["versions"]
    assert history[0]["superseded"] == "2026-10-23"
    assert history[1]["recorded"] == "2026-10-23"
