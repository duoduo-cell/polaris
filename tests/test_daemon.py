from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import polaris.daemon as daemon
from polaris.db.models import Posting, Run
from polaris.db.session import session_scope
from polaris.ingest import RawItemIn, ingest_raw_items
from polaris.search import index, maintenance


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
    logs = []

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

    daemon.run_round("maimai", None, cap=10, used=0, echo=logs.append)
    daemon.run_round("maimai", None, cap=10, used=0, echo=logs.append)

    assert calls == ["refresh", "proactive"]
    assert any("抽取 0 条，失败 1 条" in line for line in logs)
    assert any("抽取 1 条，失败 0 条" in line for line in logs)


def test_official_daily_logs_extraction_failures(db, monkeypatch):
    logs = []

    monkeypatch.setattr(daemon, "official_pending_sources", lambda _: {"tencent"})
    monkeypatch.setattr("polaris.collectors.queries.ensure_seeded", lambda _: None)
    monkeypatch.setattr("polaris.collectors.queries.existing", lambda _: ["q1", "q2"])
    monkeypatch.setattr("polaris.collectors.tencent.TencentCollector", lambda: object())
    monkeypatch.setattr(daemon, "collect_round", lambda *a, **k: {"new_ids": [1, 2], "run_id": 30})
    monkeypatch.setattr("polaris.extract.run_extract", lambda *a, **k: {"extracted": 0, "failed": 2})
    monkeypatch.setattr(daemon, "maintain_knowledge", lambda *a: None)

    daemon.official_daily(logs.append)

    assert any("新写入 2 条，抽取 0 条，失败 2 条" in line for line in logs)


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


def test_daemon_polling_delay_after_cooldown_is_ten_minutes(db, monkeypatch):
    """A 5-minute retry cooldown is observed on the next 10-minute idle poll, without a real sleep."""
    start = datetime(2026, 10, 21, 0, 20, tzinfo=UTC)  # 08:20 Beijing time; before official collection.

    class Clock:
        current = start

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)

    class OneFailureEmbedding:
        settings = SimpleNamespace(embed_model="synthetic-embedding")

        def __init__(self):
            self.calls = 0

        def embed(self, texts, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("controlled first-attempt failure")
            return [[1.0] + [0.0] * 1023 for _ in texts]

    fake = OneFailureEmbedding()
    monkeypatch.setattr(daemon, "datetime", Clock)
    monkeypatch.setattr(daemon, "single_instance", lambda: object())
    monkeypatch.setattr(daemon, "keep_awake", lambda _: None)
    monkeypatch.setattr(daemon, "proactive", lambda *a, **k: None)
    monkeypatch.setattr(daemon, "official_pending_sources", lambda _: set())
    monkeypatch.setattr(daemon.budget, "in_window", lambda _: False)
    monkeypatch.setattr(index, "LLM", lambda: fake)
    monkeypatch.setattr(maintenance, "_now", lambda now=None: now or Clock.current)
    raw_id = ingest_raw_items([RawItemIn(source="manual", kind="job", is_synthetic=True,
                                         raw_text="Synthetic scheduler retry Agent internship Shanghai")]).ids[0]
    with session_scope() as session:
        session.add(Posting(raw_item_id=raw_id, company_name="Synthetic scheduler company",
                            title="Synthetic scheduler retry Agent internship", city="Shanghai",
                            job_type="intern", jd_text="Synthetic scheduler retry Agent internship"))
    maintenance.request_refresh("synthetic scheduler timing", now=start)

    observations = []
    real_maintain = daemon.maintain_knowledge

    def observe_maintenance(echo):
        observed_at = Clock.current
        real_maintain(echo)
        observations.append((observed_at, maintenance.status()["status"]))

    monkeypatch.setattr(daemon, "maintain_knowledge", observe_maintenance)
    sleeps = []

    def controlled_sleep(seconds):
        sleeps.append(seconds)
        assert seconds == 600
        if len(sleeps) == 1:
            Clock.current += timedelta(seconds=seconds)
            return
        raise KeyboardInterrupt("stop after observing the retry")

    monkeypatch.setattr(daemon.time, "sleep", controlled_sleep)
    with pytest.raises(KeyboardInterrupt, match="stop after observing the retry"):
        daemon.loop(idle_minutes=10, echo=lambda _: None)

    assert [at for at, _ in observations] == [start, start + timedelta(minutes=10)]
    assert [status for _, status in observations] == ["pending", "ok"]
    assert maintenance.RETRY_DELAY == timedelta(minutes=5)
    assert sleeps == [600, 600]
