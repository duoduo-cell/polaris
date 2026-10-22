"""真实隔离库验证本轮新原文与历史补抽的公平批次，模型均为替身。"""
from types import SimpleNamespace

from sqlalchemy import select

from polaris import extract
from polaris.db.models import Post, Posting, Run, SearchChunk
from polaris.db.session import session_scope
from polaris.ingest import RawItemIn, ingest_raw_items
from polaris.search import index, maintenance
from polaris.search.retrieve import SearchQuery, keyword, vector


def collect_run(source="manual"):
    with session_scope() as session:
        run = Run(kind="collect", params={"source": source}, status="ok")
        session.add(run)
        session.flush()
        return run.id


def raw_batch(label, count, *, run_id=None, source="manual"):
    return ingest_raw_items([
        RawItemIn(source=source, kind="post", is_synthetic=True, raw_text=f"{label} Agent 实习，合成队列样本 {i}")
        for i in range(count)
    ], run_id).ids


def fake_extractor(monkeypatch, *, fail_ids=()):
    calls = []

    def run(llm, item, run_id=None):
        calls.append(item.id)
        if item.id in fail_ids:
            return None
        return {"raw_item_id": item.id, "post_type": "面经", "summary": item.raw_text,
                "published_at": item.created_at}

    monkeypatch.setattr(extract, "extract_post", run)
    return calls


def test_current_collect_batch_is_extracted_and_retrievable_before_old_backlog(db, monkeypatch):
    old = raw_batch("Oldneedle", 50)
    run_id = collect_run()
    fresh = raw_batch("Freshneedle", 20, run_id=run_id)
    calls = fake_extractor(monkeypatch)
    result = extract.run_extract("post", len(fresh) + 10, run_id, 1, "manual", llm=object())

    assert set(fresh) <= set(calls)  # 修前只能处理最老的30条，当前20条全部排在后面。
    assert len(calls) == len(set(calls)) == 30
    assert set(calls) & set(old) == set(old[:10])
    assert result["extracted"] == 30 and result["failed"] == 0

    class Embedding:
        settings = SimpleNamespace(embed_model="synthetic-queue-embedding")

        def embed(self, texts, **kwargs):
            return [[1.0] + [0.0] * 1023 for _ in texts]

    monkeypatch.setattr(index, "LLM", Embedding)
    assert maintenance.run_pending()["status"] == "ok"
    q = SearchQuery("Freshneedle", scope="posts", include_synthetic=True)
    with session_scope() as session:
        chunks = set(session.scalars(select(SearchChunk.id).join(Post, Post.id == SearchChunk.post_id)
                                     .where(Post.raw_item_id.in_(fresh))))
        assert chunks and len(chunks) == len(fresh)
        assert chunks <= {cid for cid, _ in keyword(session, q, q.text)}
        assert chunks <= {cid for cid, _ in vector(session, q, [1.0] + [0.0] * 1023)}


def test_new_batch_failure_keeps_raw_and_does_not_block_old_slots(db, monkeypatch):
    old = raw_batch("Oldretry", 12)
    run_id = collect_run()
    fresh = raw_batch("Freshretry", 2, run_id=run_id)
    calls = fake_extractor(monkeypatch, fail_ids=set(fresh))
    result = extract.run_extract("post", 12, run_id, 1, "manual", llm=object())
    assert calls[:2] == fresh and set(old[:10]) <= set(calls)
    assert result["failed"] == 2 and result["extracted"] == 10
    assert set(fresh) <= {item.id for item in extract.pending("post", 100, "manual")}
    calls = fake_extractor(monkeypatch)
    result = extract.run_extract("post", 12, run_id, 1, "manual", llm=object())
    assert calls[:2] == fresh and result["extracted"] == 4
    with session_scope() as session:
        assert len(list(session.scalars(select(Post)))) == len(old) + len(fresh)


def test_empty_current_batch_falls_back_to_fifo_and_respects_source(db, monkeypatch):
    raw_batch("OtherSource", 4, source="other")
    old = raw_batch("OnlyBacklog", 4)
    run_id = collect_run()
    calls = fake_extractor(monkeypatch)
    result = extract.run_extract("post", 3, run_id, 1, "manual", llm=object())
    assert calls == old[:3] and result["pending"] == 3


def test_small_budget_reserves_old_slot_and_never_duplicates(db, monkeypatch):
    old = raw_batch("Smallold", 4)
    run_id = collect_run()
    fresh = raw_batch("Smallfresh", 4, run_id=run_id)
    calls = fake_extractor(monkeypatch)
    extract.run_extract("post", 2, run_id, 1, "manual", llm=object())
    assert calls == [fresh[0], old[0]]


def test_unused_backlog_capacity_returns_to_current_batch(db, monkeypatch):
    run_id = collect_run()
    fresh = raw_batch("OnlyFresh", 12, run_id=run_id)
    calls = fake_extractor(monkeypatch)
    result = extract.run_extract("post", 12, run_id, 1, "manual", llm=object())
    assert set(calls) == set(fresh) and len(calls) == 12 and result["extracted"] == 12


def test_without_run_id_preserves_existing_fifo(db, monkeypatch):
    old = raw_batch("ManualOld", 3)
    run_id = collect_run()
    raw_batch("ManualFresh", 2, run_id=run_id)
    calls = fake_extractor(monkeypatch)
    extract.run_extract("post", 2, None, 1, "manual", llm=object())
    assert calls == old[:2]


def test_job_batch_priority_uses_same_budget_and_keeps_failed_item_pending(db, monkeypatch):
    old = ingest_raw_items([RawItemIn(source="manual", kind="job", is_synthetic=True,
                                     raw_text=f"合成旧岗位 {i}") for i in range(10)]).ids
    run_id = collect_run()
    fresh = ingest_raw_items([RawItemIn(source="manual", kind="job", is_synthetic=True,
                                       raw_text=f"合成新岗位 {i}") for i in range(5)], run_id).ids
    calls = []

    def run(llm, item, run_id=None):
        calls.append(item.id)
        return None if item.id == fresh[0] else {
            "raw_item_id": item.id, "company_name": "合成公司", "title": item.raw_text, "is_synthetic": True}

    monkeypatch.setattr(extract, "extract_job", run)
    result = extract.run_extract("job", 7, run_id, 1, "manual", llm=object())
    assert calls == fresh + old[:2]
    assert result["extracted"] == 6 and result["failed"] == 1
    assert fresh[0] in {item.id for item in extract.pending("job", 100, "manual")}
    with session_scope() as session:
        assert len(list(session.scalars(select(Posting)))) == 6
