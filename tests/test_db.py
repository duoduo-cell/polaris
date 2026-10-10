"""数据库：迁移、入库去重、运行记录、调用记录和缓存查询。需要 docker compose up -d db。"""
import pytest
from sqlalchemy import inspect, select

from polaris.db.models import Run
from polaris.db.session import session_scope
from polaris.ingest import RawItemIn, content_hash, ingest_raw_items
from polaris.llm.recorder import DbRecorder
from polaris.runs import run_scope


def test_migration_creates_all_tables(db):
    names = set(inspect(db).get_table_names())
    assert {"raw_items", "postings", "jobs", "companies", "llm_calls", "runs"} <= names


def test_ingest_is_idempotent(db):
    items = [RawItemIn(source="manual", raw_text="岗位 A"), RawItemIn(source="manual", raw_text="岗位 B")]
    first = ingest_raw_items(items)
    second = ingest_raw_items(items)
    assert (first.inserted, first.duplicates) == (2, 0)
    assert (second.inserted, second.duplicates) == (0, 2)


def test_whitespace_only_difference_is_a_duplicate_but_other_source_is_not(db):
    assert content_hash("岗位  A\n") == content_hash("岗位 A")
    ingest_raw_items([RawItemIn(source="manual", raw_text="岗位 A")])
    assert ingest_raw_items([RawItemIn(source="manual", raw_text=" 岗位\tA ")]).inserted == 0
    assert ingest_raw_items([RawItemIn(source="tavily", raw_text="岗位 A")]).inserted == 1


def test_run_scope_records_status_and_stats(db):
    with run_scope("test") as (run_id, stats):
        stats["n"] = 3
    with pytest.raises(ValueError), run_scope("test") as (failed_id, _):
        raise ValueError("boom")
    with session_scope() as s:
        ok, bad = s.get(Run, run_id), s.get(Run, failed_id)
        assert (ok.status, ok.stats, ok.finished_at is not None) == ("ok", {"n": 3}, True)
        assert bad.status == "failed" and "boom" in bad.stats["error"]


def test_db_recorder_and_cache_lookup(db):
    rec = DbRecorder()
    base = {"purpose": "t", "provider": "p", "model": "m", "endpoint": "chat", "request": {}, "latency_ms": 1,
            "cache_key": "k1"}
    rec.record({**base, "response": {"v": 1}, "parsed_ok": False, "error": "validation: bad"})
    assert rec.find_cached("k1") is None  # 不合格的不当缓存
    rec.record({**base, "response": {"v": 2}, "parsed_ok": True})
    assert rec.find_cached("k1") == {"v": 2}
    assert rec.find_cached("other") is None
    with session_scope() as s:
        assert len(s.execute(select(Run)).all()) == 0
