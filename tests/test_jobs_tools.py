"""jobs_* / posts_*：按字段筛、大厂叫法归一、只看大厂、编号出错给改法；M4 起查合并后的岗位，有 query 时按检索的相关度排。"""
import json
from datetime import UTC, date, datetime

from sqlalchemy import select

from polaris.align.build import run_align
from polaris.db.models import Post, Posting, SearchChunk
from polaris.db.session import session_scope
from polaris.ingest import RawItemIn, ingest_raw_items
from polaris.tools import REGISTRY
from polaris.tools import jobs as jobs_tools
from polaris.tools.runtime import ToolRuntime


def seed() -> dict[str, int]:
    ids = ingest_raw_items([
        RawItemIn(source="bytedance:campus", raw_text="Agent开发实习生 - 泛抖音\n杭州"),
        RawItemIn(source="bytedance:campus", raw_text="Agent开发实习生 - 泛抖音\n北京"),
        RawItemIn(source="tencent:careers", raw_text="Coding Agent Research Intern\n新加坡"),
        RawItemIn(source="manual", raw_text="某某小科技 Agent 实习\n杭州"),
        RawItemIn(source="xhs", tier="B", kind="post", raw_text="滴滴后端一面面经，问了 RAG 怎么召回"),
    ]).ids
    with session_scope() as s:
        postings = [
            Posting(raw_item_id=ids[0], company_name="字节跳动", big_company="字节跳动", title="Agent开发实习生 - 泛抖音",
                    city="杭州", job_type="intern", directions=["Agent"], duration_months=3, days_per_week=4,
                    jd_text="做 Agent"),
            Posting(raw_item_id=ids[1], company_name="字节跳动", big_company="字节跳动", title="Agent开发实习生 - 泛抖音",
                    city="北京", job_type="intern", directions=["Agent"], duration_months=3, days_per_week=5,
                    jd_text="做 Agent"),
            Posting(raw_item_id=ids[2], company_name="腾讯（IEG）", big_company="腾讯",
                    title="Coding Agent Research Intern", city="新加坡", job_type="intern",
                    posted_at=date(2026, 10, 9), jd_text="coding agent"),
            Posting(raw_item_id=ids[3], company_name="某某小科技", title="Agent 实习", city="杭州", job_type="intern")]
        post = Post(raw_item_id=ids[4], post_type="面经", companies=["滴滴"], big_companies=["滴滴"],
                    summary="滴滴后端一面，问 RAG", interview_rounds=1, interview_questions=["RAG 怎么召回"],
                    published_at=datetime(2026, 10, 10, tzinfo=UTC))
        s.add_all([*postings, post])
        s.flush()
        pids = [p.id for p in postings]
        post_id = post.id
    run_align()
    with session_scope() as s:
        job_of = dict(s.execute(select(Posting.id, Posting.job_id).where(Posting.id.in_(pids))).all())
    return {"bd": job_of[pids[0]], "bd_bj": job_of[pids[1]], "tx": job_of[pids[2]], "small": job_of[pids[3]],
            "post": post_id}


def test_jobs_search_filters_and_aliases(db):
    ids = seed()
    assert ids["bd"] == ids["bd_bj"]  # 同一岗位两个城市挂牌，合并成一个
    rt = ToolRuntime()
    out = rt.invoke(REGISTRY["jobs_search"], {"company": "TikTok", "city": "杭州"})
    items = json.loads(out)["items"]
    assert f"J-{ids['bd']}" in out and all("腾讯" not in i["company"] for i in items) and '"listings": 2' in out
    out = rt.invoke(REGISTRY["jobs_search"], {"city": "北京"})
    assert f"J-{ids['bd']}" in out  # 合并后城市取并集
    out = rt.invoke(REGISTRY["jobs_search"], {"city": "杭州"})
    assert f"J-{ids['small']}" not in out  # 默认只看大厂
    out = rt.invoke(REGISTRY["jobs_search"], {"city": "杭州", "big_only": False})
    assert f"J-{ids['small']}" in out
    out = rt.invoke(REGISTRY["jobs_search"], {"company": "美团"})
    empty = json.loads(out)
    assert empty["returned"] == 0 and empty["search"]["status"] == "scope_empty"
    assert empty["scope"]["company"] == "美团" and empty["search"]["action"] == "stop"


def test_jobs_search_by_days_per_week_reports_jds_that_do_not_say(db):
    """10-12 回答评测：问"只要去 2 天的那个"时，模型以前只能拿"每周2天"做全文检索，换说法搜了 16 次。"""
    ids = seed()
    rt = ToolRuntime()
    unknown = json.loads(rt.invoke(REGISTRY["jobs_search"], {"max_days_per_week": 3}))
    # 写明 4、5 天的被排除；腾讯那条没写天数，保留并标待确认（10-24：原来整批排除，北京实习只剩 1 个）
    assert [i["id"] for i in unknown["items"]] == [f"J-{ids['tx']}"]
    days_check = next(c for c in unknown["items"][0]["match"]["checks"] if c["key"] == "days")
    assert days_check["ok"] is None
    assert unknown["days_filter"]["jd_without_days"] == 1 and unknown["days_filter"]["least_days_in_jd"] >= 4
    some = json.loads(rt.invoke(REGISTRY["jobs_search"], {"max_days_per_week": 4}))
    assert [i["id"] for i in some["items"]] == [f"J-{ids['bd']}", f"J-{ids['tx']}"]  # 写明符合的排在待确认前面
    assert "days_filter" not in json.loads(rt.invoke(REGISTRY["jobs_search"], {"company": "字节"}))


def test_jobs_search_with_query_keeps_retrieval_order(db, monkeypatch):
    ids = seed()
    scopes = []

    def ranked(q, scope, company, job_ids=None):
        scopes.append(set(job_ids))
        return [ids["tx"], ids["bd"]], None

    monkeypatch.setattr(jobs_tools, "ranked_ids", ranked)
    out = ToolRuntime().invoke(REGISTRY["jobs_search"], {"query": "Coding Agent 研究实习"})
    assert out.index(f"J-{ids['tx']}") < out.index(f"J-{ids['bd']}")
    out = ToolRuntime().invoke(REGISTRY["jobs_search"], {"query": "Coding Agent 研究实习", "company": "字节"})
    assert f"J-{ids['tx']}" not in out and f"J-{ids['bd']}" in out  # 检索结果仍按字段筛
    assert scopes[-1] == {ids["bd"], ids["bd_bj"]}  # 只在结构化范围里排，不先取全库前几名再取交集


def test_search_ranks_only_inside_given_jobs(db, monkeypatch):
    """10-24 整链实测：北京 Agent 实习范围里 40 个候选，全库前 50 块取交集后只剩 1 个。"""
    from polaris.search.index import sync_chunks
    from polaris.search.retrieve import SearchQuery, search

    ids = seed()
    sync_chunks()
    hits, _ = search(SearchQuery("Agent 实习", scope="jobs", channels=("keyword",), rerank=False, mmr=False, top_k=50,
                                 job_ids=(ids["tx"],)))
    assert hits and {h.job_id for h in hits} == {ids["tx"]}


def test_search_falls_back_to_keywords_when_the_embedding_service_fails(db, monkeypatch):
    """10-12：向量服务欠费返回 400（不是 LLMError），以前整个工具报"内部错误"，模型换说法重试了 14 次。
    现在退回关键词和图谱，结果里说明打了折扣。"""
    from polaris.llm.client import LLM
    from polaris.search import retrieve

    ids = seed()
    from polaris.search.index import sync_chunks

    sync_chunks()

    def broken(*a, **k):
        raise RuntimeError("Error code: 400 - Arrearage")

    monkeypatch.setattr(LLM, "embed", broken)
    monkeypatch.setattr(LLM, "rerank", broken)
    monkeypatch.setattr(retrieve, "_QUERY_VECTORS", {})
    rt = ToolRuntime()
    out = json.loads(rt.invoke(REGISTRY["jobs_search"], {"query": "Coding Agent", "big_only": False}))
    assert rt.calls[-1]["ok"] and f"J-{ids['tx']}" in [i["id"] for i in out["items"]]
    assert "关键词" in out["search_note"] and "不要换说法反复搜" in out["search_note"]
    posts = json.loads(rt.invoke(REGISTRY["posts_search"], {"query": "RAG 召回"}))
    assert rt.calls[-1]["ok"] and "search_note" in posts
    questions = json.loads(rt.invoke(REGISTRY["questions_search"], {"query": "RAG 召回"}))
    assert rt.calls[-1]["ok"] and "search_note" in questions


def test_jobs_get_shows_sources_conflicts_and_bad_id(db):
    ids = seed()
    rt = ToolRuntime()
    assert "coding agent" in rt.invoke(REGISTRY["jobs_get"], {"job_id": f"J-{ids['tx']}", "response_format": "detailed"})
    out = rt.invoke(REGISTRY["jobs_get"], {"job_id": f"J-{ids['bd']}"})
    assert out.count("bytedance:campus") >= 2 and "days_per_week" in out and "得分相同，取最近采到的" in out
    bad = rt.invoke(REGISTRY["jobs_get"], {"job_id": "第二个"})
    assert bad.startswith("出错了") and "J-12" in bad


def test_posts_search_and_get(db):
    ids = seed()
    rt = ToolRuntime()
    out = rt.invoke(REGISTRY["posts_search"], {"company": "滴滴", "post_type": "面经"})
    assert f"P-{ids['post']}" in out
    assert "RAG 怎么召回" in rt.invoke(REGISTRY["posts_get"], {"post_id": f"P-{ids['post']}"})


def test_chunks_sync_keyword_search_and_graph_rows(db):
    from polaris.graph.build import collect
    from polaris.search.index import sync_chunks
    from polaris.search.retrieve import SearchQuery, search

    ids = seed()
    first = sync_chunks()
    assert first["new"] == first["total"] >= 4 and sync_chunks()["unchanged"] == first["total"]  # 重跑不重复写
    hits, trace = search(SearchQuery("coding agent", scope="jobs", channels=("keyword",), rerank=False))
    assert hits[0].job_id == ids["tx"] and trace["recall"]["keyword"] >= 1  # pg_search BM25（jieba 分词）
    hits, _ = search(SearchQuery("RAG 召回", scope="questions", channels=("keyword",), rerank=False))
    assert hits and hits[0].kind == "question"
    g = collect()
    assert {"job": ids["tx"], "city": "新加坡"} in g["job_city"]
    assert any(r["company"] == "滴滴" for r in g["question_company"])


def test_graph_counts_only_open_jobs_and_examples_are_checked_against_this_database(db):
    """10-12：官网重采会标下架，下架的不进图谱；图谱只有一份，例子编号先到当前库核对（评测库、沙盒是早先复制的）。"""
    from polaris.db.models import Job
    from polaris.graph.build import collect
    from polaris.tools.knowledge import keep_live

    ids = seed()
    with session_scope() as s:
        s.get(Job, ids["tx"]).status = "gone"
    assert all(r["job"] != ids["tx"] for r in collect()["job_city"])
    groups, stale = keep_live("jobs", [{"group": "杭州", "count": 3, "examples": [f"J-{ids['bd']}", f"J-{ids['tx']}", "J-999999"]}])
    assert groups[0]["examples"] == [f"J-{ids['bd']}"] and stale == 2


def test_graph_recall_obeys_all_filters_before_fusion(db, monkeypatch):
    from polaris.search import retrieve

    recent = datetime(2026, 10, 10, tzinfo=UTC)
    specs = {
        "old": {"published_at": datetime(2026, 9, 1, tzinfo=UTC)},
        "undated": {"published_at": None},
        "synthetic": {"is_synthetic": True},
        "other_company": {"companies": ["字节跳动"]},
        "job": {"kind": "job"},
        "valid": {},
    }
    ids = {}
    with session_scope() as s:
        for key, overrides in specs.items():
            fields = dict(kind="question", companies=["腾讯"], published_at=recent, is_synthetic=False)
            chunk = SearchChunk(key=key, header="面试题", body="RAG", content="RAG", content_hash=key,
                                **{**fields, **overrides})
            s.add(chunk)
            s.flush()
            ids[key] = chunk.id
    monkeypatch.setattr(retrieve, "graph", lambda q: [(cid, 1.0) for cid in ids.values()])
    q = retrieve.SearchQuery("RAG", scope="questions", company="Tencent", since=recent,
                             channels=("graph",), rerank=False, mmr=False)
    hits, trace = retrieve.search(q)
    assert [h.key for h in hits] == ["valid"]
    assert hits[0].ranks == {"graph": 1} and hits[0].fused == 1.0
    assert trace["recall"] == {"graph": 1}

    q.include_synthetic = True
    hits, _ = retrieve.search(q)
    assert [h.key for h in hits] == ["synthetic", "valid"]
    q.since = None
    hits, _ = retrieve.search(q)
    assert [h.key for h in hits] == ["old", "undated", "synthetic", "valid"]
    q.company = "美团"
    hits, trace = retrieve.search(q)
    assert hits == [] and trace["recall"] == {} and trace["fused"] == 0
