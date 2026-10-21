"""历史漏排队的只读诊断及显式恢复；只用隔离库、合成数据与假向量。"""
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text, update
from typer.testing import CliRunner

from polaris.align.build import run_align
from polaris.db.models import InterviewQuestion, Job, Post, Posting, RawItem, Run, SearchChunk
from polaris.db.session import session_scope
from polaris.ingest import RawItemIn, ingest_raw_items
from polaris.search import audit, index, maintenance
from polaris.search.retrieve import SearchQuery, keyword, vector

NOW = datetime(2026, 10, 21, 8, tzinfo=UTC)


class FakeEmbedding:
    settings = SimpleNamespace(embed_model="synthetic-audit")

    def embed(self, texts, **kwargs):
        return [[1.0] + [0.0] * 1023 for _ in texts]


def seed_job():
    rid = ingest_raw_items([RawItemIn(source="tencent:careers", is_synthetic=True,
                                    raw_text="合成诊断 Auditneedle Agent 实习 上海")]).ids[0]
    with session_scope() as s:
        s.execute(update(RawItem).where(RawItem.id == rid)
                  .values(first_seen_at=NOW, last_seen_at=NOW))
        s.add(Posting(raw_item_id=rid, company_name="腾讯", title="Auditneedle Agent 实习", city="上海",
                      job_type="intern", jd_text="Auditneedle 可靠工具调用", is_synthetic=True))
    return rid


def prepare_job():
    rid = seed_job()
    run_align()
    index.sync_chunks()
    index.embed_pending(FakeEmbedding())
    return rid


def test_empty_audit_does_not_enqueue_or_construct_llm(db, monkeypatch):
    monkeypatch.setattr(index, "LLM", lambda: pytest.fail("read-only audit constructed LLM"))
    out = audit.diagnose()
    assert out["task"]["status"] == "idle" and out["needs_refresh"] is False
    assert out["raw_pending"]["job"]["count"] == out["raw_pending"]["post"]["count"] == 0
    assert out["limitations"]
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Run)) == 0


def test_raw_unextracted_is_not_knowledge_work(db):
    ingest_raw_items([RawItemIn(source="manual", raw_text="合成未抽取岗位", is_synthetic=True),
                      RawItemIn(source="manual", kind="post", raw_text="合成未抽取面经", is_synthetic=True)])
    out = audit.diagnose()
    assert out["raw_pending"]["job"]["count"] == out["raw_pending"]["post"]["count"] == 1
    assert out["raw_pending"]["job"]["oldest_at"]
    assert not out["needs_refresh"]
    assert audit.enqueue_refresh()["queued"] is False
    assert maintenance.status()["status"] == "idle"


def test_historical_unaligned_data_can_be_queued_without_executing(db, monkeypatch):
    seed_job()
    monkeypatch.setattr(maintenance, "_execute", lambda stage: pytest.fail("enqueue executed stage"))
    out = audit.diagnose()
    assert out["task"]["status"] == "idle" and out["needs_refresh"]
    assert out["alignment"]["missing_jobs"] == out["alignment"]["mislinked_postings"] == 1
    queued = audit.enqueue_refresh()
    assert queued["queued"] and maintenance.status()["status"] == "pending"
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Job)) == 0


def test_real_recovery_finishes_historical_backlog_and_becomes_noop(db, monkeypatch):
    seed_job()
    monkeypatch.setattr(index, "LLM", FakeEmbedding)
    assert audit.enqueue_refresh()["queued"]
    assert maintenance.run_pending()["status"] == "ok"
    out = audit.diagnose()
    assert not out["needs_refresh"] and out["embeddings"]["unavailable"] == 0
    assert not audit.enqueue_refresh()["queued"]
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Run)) == 1
        q = SearchQuery("Auditneedle", scope="jobs", include_synthetic=True)
        expected_id = s.scalar(select(SearchChunk.id))
        assert [cid for cid, _ in keyword(s, q, q.text)] == [expected_id]
        assert vector(s, q, FakeEmbedding().embed([""])[0]) == [(expected_id, 1.0)]


def test_new_official_snapshot_detects_outdated_status_even_with_complete_vectors(db):
    prepare_job()
    assert not audit.diagnose()["needs_refresh"]
    # 直接写历史成功快照，不用 run_scope：重现旧版本没有排队的历史状态。
    with session_scope() as s:
        s.add(Run(kind="collect", status="ok", started_at=NOW + timedelta(days=3),
                  params={"mode": "official-daily", "source": "tencent"}))
    out = audit.diagnose()
    assert out["alignment"]["changed_jobs"] == 1 and out["needs_refresh"]
    assert out["embeddings"]["unavailable"] == 0
    with session_scope() as s:
        assert s.scalar(select(Job.status)) == "active"  # 诊断未修改


def test_chunk_metadata_only_change_is_detected_and_repaired_without_reembedding(db):
    prepare_job()
    with session_scope() as s:
        original = s.scalar(select(SearchChunk))
        chunk_id, content_hash = original.id, original.content_hash
        s.execute(update(Posting).values(posted_at=NOW.date()))
    run_align()
    assert audit.diagnose()["chunks"]["changed"] == 1
    q = SearchQuery("Auditneedle", scope="jobs", since=NOW.replace(hour=0), include_synthetic=True)
    with session_scope() as s:
        assert keyword(s, q, q.text) == []
        assert vector(s, q, FakeEmbedding().embed([""])[0]) == []
    index.sync_chunks()
    assert not audit.diagnose()["needs_refresh"]
    assert index.sync_chunks()["unchanged"] == 1
    assert index.embed_pending(FakeEmbedding())["embedded"] == 0
    with session_scope() as s:
        c = s.get(SearchChunk, chunk_id)
        assert c.published_at.date() == NOW.date()
        assert c.content_hash == c.embedded_hash == content_hash
        assert [cid for cid, _ in keyword(s, q, q.text)] == [chunk_id]
        assert vector(s, q, FakeEmbedding().embed([""])[0]) == [(chunk_id, 1.0)]


def test_missing_embedding_with_matching_hash_is_unavailable_and_recoverable(db):
    prepare_job()
    with session_scope() as s:
        s.execute(update(SearchChunk).values(embedding=None))
        assert vector(s, SearchQuery("Auditneedle", include_synthetic=True), FakeEmbedding().embed([""])[0]) == []
    assert audit.diagnose()["embeddings"]["unavailable"] == 1
    assert index.embed_pending(FakeEmbedding())["embedded"] == 1
    assert not audit.diagnose()["needs_refresh"]
    with session_scope() as s:
        assert vector(s, SearchQuery("Auditneedle", include_synthetic=True), FakeEmbedding().embed([""])[0])


def test_post_questions_and_chunks_are_audited_without_mutating_profile(db):
    rid = ingest_raw_items([RawItemIn(source="manual", kind="post", raw_text="合成面经：一面问了如何做缓存？",
                                    is_synthetic=True)]).ids[0]
    with session_scope() as s:
        s.add(Post(raw_item_id=rid, post_type="面经", summary="合成", interview_questions=["如何做缓存？"]))
    out = audit.diagnose()
    assert out["chunks"]["missing"] == 2 and out["questions"]["missing"] == 1
    with session_scope() as s:
        assert s.scalar(select(Post.evidence_profile)) == {}
        assert s.scalar(select(func.count()).select_from(InterviewQuestion)) == 0
    index.sync_chunks()
    index.embed_pending(FakeEmbedding())
    assert not audit.diagnose()["needs_refresh"]
    with session_scope() as s:
        s.execute(update(InterviewQuestion).values(material="study"))
    assert audit.diagnose()["questions"]["changed"] == 1


def test_enqueue_does_not_reset_exhausted_budget(db, monkeypatch):
    seed_job()
    maintenance.request_refresh("synthetic", now=NOW)
    monkeypatch.setattr(maintenance, "_execute", lambda stage: (_ for _ in ()).throw(RuntimeError("controlled")))
    for attempt in range(maintenance.MAX_ATTEMPTS):
        maintenance.run_pending(now=NOW + attempt * maintenance.RETRY_DELAY)
    before = maintenance.status()
    out = audit.enqueue_refresh()
    assert not out["queued"] and out["reason"] == "exhausted"
    assert maintenance.status() == before


def test_manual_jobs_require_explicit_prune_acknowledgement(db):
    prepare_job()
    with session_scope() as s:
        s.add(Job(key="manual-import", title="合成手工岗位", first_seen_at=NOW, last_seen_at=NOW))
    out = audit.diagnose()
    assert out["alignment"]["stale_jobs"] == 1
    assert audit.enqueue_refresh()["reason"] == "prune_requires_acknowledgement"
    assert maintenance.status()["status"] == "idle"
    assert audit.enqueue_refresh(allow_prune=True)["queued"]
    with session_scope() as s:
        assert s.scalar(select(Job).where(Job.key == "manual-import")) is not None


def test_cli_diagnosis_and_enqueue_have_distinct_effects(db):
    from polaris.cli import app

    seed_job()
    cli = CliRunner()
    out = cli.invoke(app, ["knowledge-diagnose"])
    assert out.exit_code == 0, out.exception
    assert json.loads(out.stdout)["task"]["status"] == "idle"
    out = cli.invoke(app, ["knowledge-enqueue"])
    assert out.exit_code == 0, out.exception
    assert json.loads(out.stdout)["queued"]


def test_diagnosis_uses_read_only_repeatable_snapshot_during_concurrent_commit(db, monkeypatch):
    prepare_job()
    real_build = audit.build_chunks

    def concurrent_change(session):
        assert session.scalar(text("SHOW transaction_read_only")) == "on"
        assert session.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        assert session.scalar(text("SHOW statement_timeout")) == "30s"
        with session_scope() as writer:
            writer.execute(update(Job).values(jd_text="合成并发改写 Changedneedle"))
        return real_build(session)

    monkeypatch.setattr(audit, "build_chunks", concurrent_change)
    assert not audit.diagnose()["needs_refresh"]  # 本次只能看到原快照，不混入并发新版本
    monkeypatch.setattr(audit, "build_chunks", real_build)
    out = audit.diagnose()
    assert out["alignment"]["changed_jobs"] == out["chunks"]["changed"] == 1


def test_repeated_enqueue_preserves_pending_checkpoints_and_budget(db, monkeypatch):
    seed_job()
    maintenance.request_refresh("synthetic", now=NOW)

    def fail_after_align(stage):
        if stage == "align":
            return run_align()
        raise RuntimeError("controlled chunks failure")

    monkeypatch.setattr(maintenance, "_execute", fail_after_align)
    maintenance.run_pending(now=NOW)
    before = maintenance.status()
    assert before["stats"]["stages"]["align"]["state"] == "ok"
    assert before["stats"]["attempts"]["chunks"] == 1
    out = audit.enqueue_refresh()
    assert not out["queued"] and out["reason"] == "already_pending"
    assert maintenance.status() == before


@pytest.mark.parametrize("has_posting", [False, True])
def test_null_key_job_prune_diagnosis_matches_actual_alignment(db, has_posting):
    if has_posting:
        prepare_job()
    with session_scope() as s:
        job = Job(key=None, title="合成无编号岗位", first_seen_at=NOW, last_seen_at=NOW)
        s.add(job)
        s.flush()
        job_id = job.id
    out = audit.diagnose()
    assert out["alignment"]["stale_jobs"] == 0
    assert out["unmanaged_jobs"] == 1
    result = run_align()
    assert result["stale_removed"] == 0
    with session_scope() as s:
        assert s.get(Job, job_id) is not None


def test_stale_chunk_is_detected_and_removed_by_recovery(db, monkeypatch):
    prepare_job()
    with session_scope() as s:
        s.add(SearchChunk(key="synthetic-orphan", kind="post", companies=[], header="合成", body="过时",
                          content="合成过时", content_hash="old", is_synthetic=True))
    out = audit.diagnose()
    assert out["chunks"]["stale"] == 1
    assert audit.enqueue_refresh()["queued"]
    monkeypatch.setattr(index, "LLM", FakeEmbedding)
    assert maintenance.run_pending()["status"] == "ok"
    assert not audit.diagnose()["needs_refresh"]


def test_equivalent_non_utc_date_is_not_reported_as_changed(db):
    prepare_job()
    with session_scope() as s:
        s.execute(update(Posting).values(posted_at=NOW.date()))
    run_align()
    index.sync_chunks()
    # PostgreSQL timestamptz 的显示时区不得造成重复更新或误报。
    with session_scope() as s:
        s.execute(text("SET LOCAL TIME ZONE 'Asia/Shanghai'"))
        chunks = index.build_chunks(s)
        old = {r["key"]: r for r in s.execute(select(*(getattr(SearchChunk, f) for f in index.CHUNK_FIELDS))).mappings()}
        assert index._sync_chunks(s, chunks, old)["unchanged"] == 1
    assert not audit.diagnose()["needs_refresh"]
