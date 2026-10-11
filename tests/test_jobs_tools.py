"""jobs_* / posts_*：按字段筛、大厂叫法归一、只看大厂、编号出错给改法；M4 起查合并后的岗位，有 query 时按检索的相关度排。"""
from datetime import UTC, date, datetime

from sqlalchemy import select

from polaris.align.build import run_align
from polaris.db.models import Post, Posting
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
    assert f"J-{ids['bd']}" in out and "腾讯" not in out and '"listings": 2' in out
    out = rt.invoke(REGISTRY["jobs_search"], {"city": "北京"})
    assert f"J-{ids['bd']}" in out  # 合并后城市取并集
    out = rt.invoke(REGISTRY["jobs_search"], {"city": "杭州"})
    assert f"J-{ids['small']}" not in out  # 默认只看大厂
    out = rt.invoke(REGISTRY["jobs_search"], {"city": "杭州", "big_only": False})
    assert f"J-{ids['small']}" in out
    out = rt.invoke(REGISTRY["jobs_search"], {"company": "美团"})
    assert '"returned": 0' in out and "如实告诉用户" in out


def test_jobs_search_with_query_keeps_retrieval_order(db, monkeypatch):
    ids = seed()
    monkeypatch.setattr(jobs_tools, "ranked_ids", lambda q, scope, company: [ids["tx"], ids["bd"]])
    out = ToolRuntime().invoke(REGISTRY["jobs_search"], {"query": "Coding Agent 研究实习"})
    assert out.index(f"J-{ids['tx']}") < out.index(f"J-{ids['bd']}")
    out = ToolRuntime().invoke(REGISTRY["jobs_search"], {"query": "Coding Agent 研究实习", "company": "字节"})
    assert f"J-{ids['tx']}" not in out and f"J-{ids['bd']}" in out  # 检索结果仍按字段筛


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
