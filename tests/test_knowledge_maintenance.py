"""真实 PostgreSQL 待办/锁/检索，模型与故障均为合成，不访问采集平台。"""
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from importlib import reload
from time import perf_counter
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from typer.testing import CliRunner

from polaris.db.models import Post, Posting, Run, SearchChunk
from polaris.db.session import session_scope
from polaris.extract import pending as pending_extractions
from polaris.extract import run_extract
from polaris.ingest import RawItemIn, ingest_raw_items
from polaris.schemas import JobFacts
from polaris.search import index
from polaris.search import maintenance as m
from polaris.search.locking import acquire_knowledge_write
from polaris.search.retrieve import SearchQuery, keyword, vector

NOW = datetime(2026, 10, 21, 8, tzinfo=UTC)
GOOD_EMBED = {"pending": 0, "embedded": 0, "failed_groups": 0, "errors": [], "remaining": 0}
BAD_EMBED = {"pending": 1, "embedded": 0, "failed_groups": 1, "errors": ["controlled failure"], "remaining": 1}


def mock_stages(monkeypatch, *, embed=None):
    calls = []

    def execute(stage):
        calls.append(stage)
        return (embed() if callable(embed) else embed or GOOD_EMBED) if stage == "embed" else {}

    monkeypatch.setattr(m, "_execute", execute)
    return calls


def test_cooldown_restart_partial_failure_and_successful_stages_not_repeated(db, monkeypatch):
    # Reload 是新 Python 模块状态；待办、次数、进度只存在数据库。
    m.request_refresh("synthetic", now=NOW)
    calls = mock_stages(monkeypatch, embed=BAD_EMBED)
    first = m.run_pending(now=NOW)
    assert first["status"] == "pending" and first["stats"]["failed_stage"] == "embed"
    assert first["stats"]["attempts"]["embed"] == 1
    assert m.run_pending(now=NOW + timedelta(seconds=1))["reason"] == "cooldown"
    assert calls == ["align", "chunks", "embed"]
    reload(m)
    calls = mock_stages(monkeypatch)
    out = m.run_pending(now=NOW + m.RETRY_DELAY)
    assert out["status"] == "ok" and calls == ["embed", "graph"]
    assert out["stats"]["stages"]["align"]["state"] == "ok"
    assert m.run_pending(now=NOW + timedelta(days=1))["ran"] is False


def test_exhaustion_survives_new_data_and_manual_retry_is_explicit(db, monkeypatch):
    m.request_refresh("synthetic", now=NOW)
    calls = mock_stages(monkeypatch, embed=BAD_EMBED)
    for attempt in range(m.MAX_ATTEMPTS):
        out = m.run_pending(now=NOW + attempt * m.RETRY_DELAY)
    assert out["status"] == "exhausted" and calls.count("embed") == 3
    task_id = out["run_id"]
    m.request_refresh("more synthetic data", now=NOW + timedelta(days=1))
    assert m.run_pending(now=NOW + timedelta(days=1))["reason"] == "exhausted"
    assert m.status()["stats"]["attempts"]["embed"] == 3
    assert m.retry(now=NOW + timedelta(days=1))["queued"]
    assert not m.retry(now=NOW + timedelta(days=1))["queued"]
    calls = mock_stages(monkeypatch)
    out = m.run_pending(now=NOW + timedelta(days=1))
    assert out["run_id"] == task_id and out["status"] == "ok"
    assert out["stats"]["manual_retries"] == 1


def test_interrupted_claim_is_persisted_and_lock_released(db, monkeypatch):
    m.request_refresh("synthetic", now=NOW)

    def interrupted(stage):
        raise KeyboardInterrupt("controlled interruption")

    monkeypatch.setattr(m, "_execute", interrupted)
    with pytest.raises(KeyboardInterrupt):
        m.run_pending(now=NOW)
    state = m.status()
    assert state["status"] == "running" and state["stats"]["attempts"]["align"] == 1
    assert m.run_pending(now=NOW)["reason"] == "cooldown"
    mock_stages(monkeypatch)
    assert m.run_pending(now=NOW + m.RETRY_DELAY)["status"] == "ok"


def test_hard_process_exit_releases_lock_and_next_process_resumes(db, monkeypatch):
    """真正退出独立 Python 进程，跳过 finally；用另一个进程验证 DB 状态和恢复。"""
    code = """
import os
from datetime import UTC, datetime
from polaris.search import maintenance as m
m.request_refresh('synthetic subprocess crash', now=datetime(2026, 10, 21, 8, tzinfo=UTC))
m._execute = lambda stage: os._exit(17)
m.run_pending(now=datetime(2026, 10, 21, 8, tzinfo=UTC))
"""
    proc = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True, timeout=20)
    assert proc.returncode == 17, proc.stderr.decode("utf-8", errors="replace")
    assert m.status()["status"] == "running"
    mock_stages(monkeypatch)
    assert m.run_pending(now=NOW + m.RETRY_DELAY)["status"] == "ok"


def test_worker_lock_prevents_two_workers_but_allows_dirty_notification(db, monkeypatch):
    m.request_refresh("synthetic", now=NOW)
    with db.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": m.WORKER_LOCK})
        try:
            assert m.run_pending(now=NOW)["reason"] == "busy"
            m.request_refresh("another synthetic source", now=NOW)
            assert m.status()["params"]["generation"] == 2
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": m.WORKER_LOCK})
    mock_stages(monkeypatch)
    assert m.run_pending(now=NOW)["status"] == "ok"


@pytest.mark.parametrize("new_generation", [False, True])
def test_derived_writer_busy_preserves_failure_allowance_and_generation(db, monkeypatch, new_generation):
    m.request_refresh("synthetic", now=NOW)
    real_execute = m._execute

    def fail(stage):
        raise RuntimeError("controlled initial failure")

    monkeypatch.setattr(m, "_execute", fail)
    assert m.run_pending(now=NOW)["stats"]["attempts"]["align"] == 1

    def execute(stage):
        if new_generation:
            m.request_refresh("new data during competing claim", now=NOW + m.RETRY_DELAY)
        return real_execute(stage)

    monkeypatch.setattr(m, "_execute", execute)
    with session_scope() as owner:
        acquire_knowledge_write(owner)
        out = m.run_pending(now=NOW + m.RETRY_DELAY)
    assert out["status"] == "pending"
    assert out["stats"]["attempts"]["align"] == 1  # 不清掉原失败，也不消耗本次额度。
    assert out["stats"]["waiting_for"] == "knowledge_writer"
    assert out["params"]["generation"] == (2 if new_generation else 1)
    assert out["stats"]["stages"] == ({} if new_generation else {
        "align": {"state": "failed", "result": {"error": "RuntimeError: controlled initial failure"}, "at": NOW.isoformat()}})
    assert m.run_pending(now=NOW + m.RETRY_DELAY)["reason"] == "cooldown"
    mock_stages(monkeypatch)
    done = m.run_pending(now=NOW + 2 * m.RETRY_DELAY)
    assert done["status"] == "ok" and "waiting_for" not in done["stats"]


def test_new_generation_during_execution_cannot_be_marked_complete(db, monkeypatch):
    m.request_refresh("synthetic", now=NOW)

    def new_data(stage):
        assert stage == "align"
        m.request_refresh("arrived during alignment", now=NOW)
        return {}

    monkeypatch.setattr(m, "_execute", new_data)
    out = m.run_pending(now=NOW)
    assert out["status"] == "pending" and out["params"]["generation"] == 2
    assert out["stats"]["stages"] == {}
    calls = mock_stages(monkeypatch)
    assert m.run_pending(now=NOW + m.RETRY_DELAY)["status"] == "ok"
    assert calls == list(m.STAGES)


def test_successful_embedding_backlog_does_not_consume_failure_allowance(db, monkeypatch):
    m.request_refresh("synthetic", now=NOW)
    partial = {"pending": 200, "embedded": 200, "failed_groups": 0, "remaining": 2, "errors": []}
    calls = mock_stages(monkeypatch, embed=partial)
    for attempt in range(m.MAX_ATTEMPTS + 1):
        out = m.run_pending(now=NOW + attempt * m.RETRY_DELAY)
        assert out["status"] == "pending" and out["stats"]["attempts"]["embed"] == 0
    assert calls.count("align") == 1 and "graph" not in calls
    mock_stages(monkeypatch)
    assert m.run_pending(now=NOW + 5 * m.RETRY_DELAY)["status"] == "ok"


@pytest.mark.parametrize("bad", [{}, {"failed_groups": 0}, {**GOOD_EMBED, "error": "oops"},
                                  {**GOOD_EMBED, "remaining": 1}])
def test_malformed_or_nonprogress_embedding_result_is_not_success(db, monkeypatch, bad):
    m.request_refresh("synthetic", now=NOW)
    monkeypatch.setattr(m, "_execute", lambda stage: bad if stage == "embed" else {})
    out = m.run_pending(now=NOW)
    assert out["status"] == "pending" and out["stats"]["failed_stage"] == "embed"


def test_optional_graph_error_preserves_core_completion(db, monkeypatch):
    m.request_refresh("synthetic", now=NOW)

    def execute(stage):
        if stage == "graph":
            raise RuntimeError("controlled graph failure")
        return GOOD_EMBED if stage == "embed" else {}

    monkeypatch.setattr(m, "_execute", execute)
    out = m.run_pending(now=NOW)
    assert out["status"] == "ok" and out["stats"]["graph_error"]
    assert out["stats"]["stages"]["graph"]["state"] == "failed"
    assert out["stats"]["core_completed_at"]


def test_upstream_failure_stops_downstream_and_is_retried(db, monkeypatch):
    m.request_refresh("synthetic", now=NOW)
    calls = []

    def execute(stage):
        calls.append(stage)
        raise RuntimeError("controlled alignment failure")

    monkeypatch.setattr(m, "_execute", execute)
    out = m.run_pending(now=NOW)
    assert calls == ["align"] and out["stats"]["failed_stage"] == "align"
    calls = mock_stages(monkeypatch)
    assert m.run_pending(now=NOW + m.RETRY_DELAY)["status"] == "ok"
    assert calls == list(m.STAGES)


def test_status_retry_and_work_cli_do_not_collect(db, monkeypatch):
    from polaris.cli import app

    calls = mock_stages(monkeypatch)
    cli = CliRunner()
    assert '"status": "idle"' in cli.invoke(app, ["knowledge-status"]).stdout
    m.request_refresh("synthetic")  # CLI 用真实时钟，不能把待办排在固定的未来时间。
    out = cli.invoke(app, ["knowledge-work"])
    assert out.exit_code == 0 and '"status": "ok"' in out.stdout
    assert cli.invoke(app, ["knowledge-retry"]).exit_code == 0
    assert calls == list(m.STAGES)


@pytest.mark.parametrize("kind", ["job", "post"])
def test_extraction_commit_also_persists_refresh_without_daemon(db, monkeypatch, kind):
    from polaris import extract

    rid = ingest_raw_items([RawItemIn(source="manual", kind=kind, is_synthetic=True,
                                     raw_text="合成事务恢复样本")]).ids[0]
    row = {"raw_item_id": rid, "company_name": "合成公司", "title": "合成岗位", "is_synthetic": True} if kind == "job" else {
        "raw_item_id": rid, "post_type": "experience", "summary": "合成面经"}
    monkeypatch.setattr(extract, f"extract_{kind}", lambda *a: row)
    assert extract.run_extract(kind, 1, None, 1, llm=object())["extracted"] == 1
    # 没有 daemon 后续调用；独立抽取、CLI 和侦察入口也必须留下待办。
    with session_scope() as s:
        target = Posting if kind == "job" else Post
        assert s.scalar(select(func.count()).select_from(target)) == 1
    assert m.status()["status"] == "pending"
    reload(m)
    mock_stages(monkeypatch)
    assert m.run_pending()["status"] == "ok"


def test_extraction_rolls_back_when_refresh_notification_cannot_be_saved(db, monkeypatch):
    from polaris import extract

    rid = ingest_raw_items([RawItemIn(source="manual", is_synthetic=True, raw_text="合成提交失败")]).ids[0]
    monkeypatch.setattr(extract, "extract_job", lambda *a: {
        "raw_item_id": rid, "company_name": "合成公司", "title": "合成岗位", "is_synthetic": True})
    monkeypatch.setattr(m, "request_refresh", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("controlled queue failure")))
    out = extract.run_extract("job", 1, None, 1, llm=object())
    assert out["extracted"] == 0 and out["failed"] == 1
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Posting)) == 0
    assert [r.id for r in extract.pending("job", 1)] == [rid]
    assert m.status()["status"] == "idle"


def test_successful_official_snapshot_queues_refresh_even_without_new_rows(db):
    from polaris.runs import run_scope

    with run_scope("collect", {"mode": "official-daily", "source": "tencent"}) as (rid, _):
        pass
    with session_scope() as s:
        assert s.get(Run, rid).status == "ok"
    assert m.status()["status"] == "pending"


def test_official_success_and_refresh_notification_commit_together(db, monkeypatch):
    from polaris.runs import run_scope

    monkeypatch.setattr(m, "request_refresh", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("controlled queue failure")))
    with pytest.raises(RuntimeError, match="controlled queue failure"):
        with run_scope("collect", {"mode": "official-daily", "source": "tencent"}) as (rid, _):
            pass
    with session_scope() as s:
        assert s.get(Run, rid).status != "ok"
    assert m.status()["status"] == "idle"


def test_repeated_interruption_of_optional_graph_does_not_exhaust_core(db, monkeypatch):
    m.request_refresh("synthetic", now=NOW)

    def execute(stage):
        if stage == "graph":
            raise KeyboardInterrupt("controlled graph interruption")
        return GOOD_EMBED if stage == "embed" else {}

    monkeypatch.setattr(m, "_execute", execute)
    for attempt in range(m.MAX_ATTEMPTS):
        with pytest.raises(KeyboardInterrupt):
            m.run_pending(now=NOW + attempt * m.RETRY_DELAY)
    out = m.run_pending(now=NOW + m.MAX_ATTEMPTS * m.RETRY_DELAY)
    assert out["status"] == "ok" and out["stats"]["graph_error"]


class FakeJobExtraction:
    def __init__(self, *, fail=False):
        self.fail = fail

    def extract(self, prompt, schema, **kwargs):
        assert schema is JobFacts
        assert "Recoveryneedle" in prompt
        if self.fail:
            return SimpleNamespace(ok=False, value=None, call_ids=[], error="controlled extraction failure")
        return SimpleNamespace(
            ok=True,
            value=JobFacts(company="合成公司", title="Recoveryneedle Agent 实习", directions=["Agent"],
                           city="上海", job_type="intern", requirements=["可靠工具调用与任务编排"]),
            call_ids=[None],
        )


class FakeEmbedding:
    settings = SimpleNamespace(embed_model="synthetic-embedding")

    def __init__(self):
        self.fail = True
        self.batch_sizes = []

    def embed(self, texts, **kw):
        self.batch_sizes.append(len(texts))
        if self.fail:
            raise RuntimeError("controlled embedding outage")
        return [[1.0] + [0.0] * 1023 for _ in texts]


def test_real_index_and_retrieval_recover_without_new_raw_items(db, monkeypatch, record_property):
    started = perf_counter()
    ingested = ingest_raw_items([RawItemIn(source="manual", kind="job", is_synthetic=True,
                                           raw_text="合成恢复样本 Recoveryneedle Agent 实习 上海；可靠工具调用与任务编排")])
    assert ingested.inserted == 1
    raw_committed = perf_counter()
    monkeypatch.setattr(m, "_now", lambda now=None: now or NOW)
    failed_extract = run_extract("job", 1, None, 1, source="manual", llm=FakeJobExtraction(fail=True))
    assert failed_extract == {"pending": 1, "extracted": 0, "failed": 1, "big_company": 0}
    assert [item.id for item in pending_extractions("job", 1, "manual")] == ingested.ids
    assert m.status()["status"] == "idle"  # 抽取失败不能伪报出知识待办或任务完成。
    extracted = run_extract("job", 1, None, 1, source="manual", llm=FakeJobExtraction())
    assert extracted == {"pending": 1, "extracted": 1, "failed": 0, "big_company": 0}
    extraction_committed = perf_counter()
    assert m.status()["status"] == "pending"  # run_extract 同事务登记知识刷新待办。
    fake = FakeEmbedding()
    monkeypatch.setattr(index, "LLM", lambda: fake)
    first = m.run_pending(now=NOW)
    assert first["status"] == "pending" and first["stats"]["failed_stage"] == "embed"
    q = SearchQuery("Recoveryneedle", scope="jobs", include_synthetic=True)
    vec = [1.0] + [0.0] * 1023
    with session_scope() as s:
        ids_before = list(s.scalars(select(SearchChunk.id).order_by(SearchChunk.id)))
        assert ids_before and keyword(s, q, q.text)
        assert vector(s, q, vec) == []
    keyword_available = perf_counter()
    fake.fail = False
    out = m.run_pending(now=NOW + m.RETRY_DELAY)
    assert out["status"] == "ok"
    with session_scope() as s:
        assert keyword(s, q, q.text) and vector(s, q, vec)
        assert list(s.scalars(select(SearchChunk.id).order_by(SearchChunk.id))) == ids_before
    vector_available = perf_counter()
    # 再请求一次，同一条原文和块不重复写，也不再调用向量服务。
    batch_count = len(fake.batch_sizes)
    m.request_refresh("idempotency check", now=NOW + timedelta(hours=1))
    assert m.run_pending(now=NOW + timedelta(hours=1))["status"] == "ok"
    with session_scope() as s:
        assert list(s.scalars(select(SearchChunk.id).order_by(SearchChunk.id))) == ids_before
    assert len(fake.batch_sizes) == batch_count
    timings = {
        "ingest_commit_ms": (raw_committed - started) * 1000,
        "extraction_commit_ms": (extraction_committed - raw_committed) * 1000,
        "keyword_available_after_ingest_ms": (keyword_available - raw_committed) * 1000,
        "vector_available_after_ingest_ms": (vector_available - raw_committed) * 1000,
    }
    for name, value in timings.items():
        record_property(name, round(value, 2))
    print("synthetic full chain wall: " + ", ".join(f"{k}={v:.2f}ms" for k, v in timings.items())
          + f"; controlled retry cooldown advanced={m.RETRY_DELAY.total_seconds():.0f}s")


def test_real_embedding_pass_is_bounded_and_reports_remaining(db):
    fake = FakeEmbedding()
    fake.fail = False
    with session_scope() as s:
        for i in range(205):
            s.add(SearchChunk(key=f"synthetic-{i}", kind="post", companies=[], header="合成",
                              body="合成", content=f"synthetic {i}", content_hash=f"h{i}", is_synthetic=True))
    out = index.embed_pending(fake, limit=m.EMBED_LIMIT)
    assert out["pending"] == out["embedded"] == 200 and out["remaining"] == 5
    assert sum(fake.batch_sizes) == 200 and max(fake.batch_sizes) <= index.EMBED_GROUP
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(SearchChunk).where(SearchChunk.embedding.is_not(None))) == 200
    out = index.embed_pending(fake, limit=m.EMBED_LIMIT)
    assert out["embedded"] == 5 and out["remaining"] == 0


def test_no_pending_chunks_does_not_construct_llm(db, monkeypatch):
    monkeypatch.setattr(index, "LLM", lambda: (_ for _ in ()).throw(AssertionError("no LLM needed")))
    assert index.embed_pending() == GOOD_EMBED
    with pytest.raises(ValueError):
        index.embed_pending(limit=0)


def test_real_partial_embedding_batch_commits_success_and_only_retries_failed_rows(db):
    with session_scope() as s:
        for i in range(51):
            s.add(SearchChunk(key=f"partial-{i}", kind="post", companies=[], header="合成",
                              body="合成", content=f"partial {i}", content_hash=f"h{i}", is_synthetic=True))

    class PartialEmbedding(FakeEmbedding):
        def embed(self, texts, **kw):
            if "partial 50" in texts:
                raise RuntimeError("controlled second group failure")
            return [[1.0] + [0.0] * 1023 for _ in texts]

    out = index.embed_pending(PartialEmbedding(), limit=200)
    assert out["embedded"] == 50 and out["failed_groups"] == 1 and out["remaining"] == 1
    fake = FakeEmbedding()
    fake.fail = False
    out = index.embed_pending(fake, limit=200)
    assert out["embedded"] == 1 and out["remaining"] == 0 and fake.batch_sizes == [1]
