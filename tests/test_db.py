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


def test_ingest_tracks_first_and_last_seen_and_simhash(db):
    from sqlalchemy import select

    from polaris.db.models import RawItem
    from polaris.db.session import session_scope
    from polaris.ingest import RawItemIn, ingest_raw_items

    item = RawItemIn(source="tencent:careers", external_id="1", raw_text="Agent 开发工程师\n深圳", fetch_method="api")
    first = ingest_raw_items([item])
    again = ingest_raw_items([item, item])  # 同一批里重复的也只算一次
    assert first.inserted == 1 and again.inserted == 0 and again.seen_again == 1
    with session_scope() as s:
        row = s.execute(select(RawItem)).scalar_one()
        assert row.last_seen_at > row.first_seen_at and row.simhash is not None and row.fetch_method == "api"


def test_simhash_separates_reposts_from_unrelated():
    from polaris.dedup import near_duplicate, simhash64

    a = "米哈游目前在紧急补招实习生，基础日薪260/天，接受无经验，可预订寒假！每周线下到岗至少4天，实习时长最低3个月，表现优秀有转正机会"
    b = a + "！！#找实习[话题]# [飞吻R]"
    c = "滴滴后端一面，主要拷打项目，四十分钟，无手撕。知识库怎么存储、建设、分层，RAG 的流程，怎么召回"
    assert near_duplicate(simhash64(a), simhash64(b)) and not near_duplicate(simhash64(a), simhash64(c))


def test_interrupted_round_still_records_calls(db):
    from dataclasses import dataclass

    import pytest
    from sqlalchemy import select

    from polaris.collectors.rounds import collect_round
    from polaris.db.models import Run
    from polaris.db.session import session_scope
    from polaris.ingest import RawItemIn

    @dataclass
    class Stats:
        calls: int = 0
        searched: int = 0

    class Flaky:
        def __init__(self):
            self.stats = Stats()

        def collect(self, query):
            self.stats.calls += 3
            self.stats.searched += 1
            yield RawItemIn(source="xhs", tier="B", kind="post", raw_text="一条笔记")
            raise KeyboardInterrupt  # 模拟中途按了 Ctrl+C

    with pytest.raises(KeyboardInterrupt):
        collect_round("xhs", Flaky(), 2, {"max_calls": 10})
    with session_scope() as s:
        run = s.execute(select(Run).where(Run.kind == "collect")).scalar_one()
        assert run.status == "failed" and run.stats["calls"] == 3 and run.stats["inserted"] == 1
