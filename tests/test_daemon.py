from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

import polaris.daemon as daemon
from polaris.db.models import Run
from polaris.db.session import session_scope


def test_run_round_backfills_pending_data_when_collection_has_no_new_ids(db, monkeypatch):
    calls = []

    class Collector:
        def close(self):
            pass

    monkeypatch.setattr(daemon.budget, "policy", lambda: {"maimai": {"round_queries": 1, "via": "api"}})
    monkeypatch.setattr(daemon.budget, "hour_left", lambda source: 10)
    monkeypatch.setattr(daemon, "FACTORIES", {"maimai": lambda *args: Collector()})
    monkeypatch.setattr("polaris.collectors.locks.platform_lock", lambda *a, **k: nullcontext())
    monkeypatch.setattr(daemon, "collect_round", lambda *a, **k: {"new_ids": [], "run_id": 7})
    monkeypatch.setattr("polaris.extract.run_extract", lambda kind, limit, run_id, concurrency, source:
                        calls.append(("extract", kind, limit, run_id, source)) or {"extracted": 1, "failed": 0})
    monkeypatch.setattr(daemon, "get_settings", lambda: SimpleNamespace(llm_concurrency=1))
    monkeypatch.setattr("polaris.peers.run", lambda limit, source: calls.append(("peers", limit, source)) or
                        {"people": 0, "with_people": 0})
    monkeypatch.setattr(daemon, "maintain_knowledge", lambda *a: calls.append(("refresh",)))
    monkeypatch.setattr(daemon, "proactive", lambda *a, **k: calls.append(("proactive",)))

    daemon.run_round("maimai", None, cap=10, used=0, echo=lambda _: None)

    assert ("extract", "post", 10, 7, "maimai") in calls
    assert ("peers", 20, "maimai") in calls
    assert ("refresh",) in calls


def test_run_round_retries_backfill_after_extraction_failure(db, monkeypatch):
    calls = []

    class Collector:
        def close(self):
            pass

    monkeypatch.setattr(daemon.budget, "policy", lambda: {"maimai": {"round_queries": 1, "via": "api"}})
    monkeypatch.setattr(daemon.budget, "hour_left", lambda source: 10)
    monkeypatch.setattr(daemon, "FACTORIES", {"maimai": lambda *args: Collector()})
    monkeypatch.setattr("polaris.collectors.locks.platform_lock", lambda *a, **k: nullcontext())
    monkeypatch.setattr(daemon, "collect_round", lambda *a, **k: {"new_ids": [], "run_id": 8})
    outcomes = iter(({"extracted": 0, "failed": 1}, {"extracted": 1, "failed": 0}))
    monkeypatch.setattr("polaris.extract.run_extract", lambda *a, **k: next(outcomes))
    monkeypatch.setattr(daemon, "get_settings", lambda: SimpleNamespace(llm_concurrency=1))
    monkeypatch.setattr("polaris.peers.run", lambda *a, **k: {"people": 0, "with_people": 0})
    monkeypatch.setattr(daemon, "maintain_knowledge", lambda *a: calls.append("refresh"))
    monkeypatch.setattr(daemon, "proactive", lambda *a, **k: calls.append("proactive"))

    daemon.run_round("maimai", None, cap=10, used=0, echo=lambda _: None)
    daemon.run_round("maimai", None, cap=10, used=0, echo=lambda _: None)

    assert calls == ["refresh", "proactive"]


def test_run_round_does_not_refresh_or_notify_without_new_or_backfilled_data(db, monkeypatch):
    calls = []

    class Collector:
        def close(self):
            pass

    monkeypatch.setattr(daemon.budget, "policy", lambda: {"maimai": {"round_queries": 1, "via": "api"}})
    monkeypatch.setattr(daemon.budget, "hour_left", lambda source: 10)
    monkeypatch.setattr(daemon, "FACTORIES", {"maimai": lambda *args: Collector()})
    monkeypatch.setattr("polaris.collectors.locks.platform_lock", lambda *a, **k: nullcontext())
    monkeypatch.setattr(daemon, "collect_round", lambda *a, **k: {"new_ids": [], "run_id": 9})
    monkeypatch.setattr("polaris.extract.run_extract", lambda *a, **k: {"extracted": 0, "failed": 0})
    monkeypatch.setattr(daemon, "get_settings", lambda: SimpleNamespace(llm_concurrency=1))
    monkeypatch.setattr("polaris.peers.run", lambda *a, **k: {"people": 0, "with_people": 0})
    monkeypatch.setattr(daemon, "maintain_knowledge", lambda *a: calls.append("refresh"))
    monkeypatch.setattr(daemon, "proactive", lambda *a, **k: calls.append("proactive"))

    daemon.run_round("maimai", None, cap=10, used=0, echo=lambda _: None)

    assert calls == []


def test_daemon_retries_partial_embedding_failure_without_new_data_or_browser(db, monkeypatch):
    """失败后不再采集，知识层仍应有持久待办并在独立 tick 补跑。"""
    calls = []
    monkeypatch.setattr(daemon.budget, "policy", lambda: {"maimai": {"round_queries": 1}})
    monkeypatch.setattr(daemon.budget, "hour_left", lambda _: 10)
    monkeypatch.setattr(daemon, "FACTORIES", {"maimai": lambda *a: SimpleNamespace(close=lambda: None)})
    monkeypatch.setattr("polaris.collectors.locks.platform_lock", lambda *a, **k: nullcontext())
    monkeypatch.setattr(daemon, "collect_round", lambda *a, **k: {"new_ids": [], "run_id": 10})
    monkeypatch.setattr("polaris.extract.run_extract", lambda *a, **k: {"extracted": 1, "failed": 0})
    monkeypatch.setattr("polaris.peers.run", lambda *a, **k: {"people": 0})
    monkeypatch.setattr(daemon, "get_settings", lambda: SimpleNamespace(llm_concurrency=1))
    monkeypatch.setattr(daemon, "proactive", lambda *a: None)
    monkeypatch.setattr("polaris.align.build.run_align", lambda: {})
    monkeypatch.setattr("polaris.search.index.sync_chunks", lambda: {})
    outcomes = iter((
        {"pending": 1, "embedded": 0, "failed_groups": 1, "errors": ["controlled embedding failure"], "remaining": 1},
        {"pending": 1, "embedded": 1, "failed_groups": 0, "errors": [], "remaining": 0},
    ))
    monkeypatch.setattr("polaris.search.index.embed_pending", lambda **kw: calls.append("embed") or next(outcomes))
    daemon.run_round("maimai", None, cap=10, used=0, echo=lambda _: None)
    # 人工推进测试时钟：只改隔离库待办的到期时间，不睡眠、不采集。
    with session_scope() as s:
        task = s.scalar(select(Run).where(Run.kind == "knowledge_refresh").order_by(Run.id.desc()))
        if task:
            task.stats = {**task.stats, "next_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
    monkeypatch.setattr(daemon.budget, "in_window", lambda _: False)
    monkeypatch.setattr(daemon, "open_browser", lambda *a: (_ for _ in ()).throw(AssertionError("must not open browser")))
    assert daemon.tick(echo=lambda _: None) is False  # 返回值只报告是否采集
    assert calls == ["embed", "embed"]
    with session_scope() as s:
        task = s.scalar(select(Run).where(Run.kind == "knowledge_refresh").order_by(Run.id.desc()))
        assert task.status == "ok"
