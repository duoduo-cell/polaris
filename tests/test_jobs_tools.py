"""jobs_* / posts_*：按字段筛、大厂叫法归一、只看大厂、编号出错给改法。"""
from datetime import UTC, date, datetime

from polaris.db.models import Post, Posting
from polaris.db.session import session_scope
from polaris.ingest import RawItemIn, ingest_raw_items
from polaris.tools import REGISTRY
from polaris.tools.runtime import ToolRuntime


def seed() -> dict[str, int]:
    ids = ingest_raw_items([
        RawItemIn(source="bytedance:campus", raw_text="Agent开发实习生 - 泛抖音\n杭州"),
        RawItemIn(source="tencent:careers", raw_text="Coding Agent Research Intern\n新加坡"),
        RawItemIn(source="manual", raw_text="某某小科技 Agent 实习\n杭州"),
        RawItemIn(source="xhs", tier="B", kind="post", raw_text="滴滴后端一面面经，问了 RAG 怎么召回"),
    ]).ids
    with session_scope() as s:
        jobs = [Posting(raw_item_id=ids[0], company_name="字节跳动", big_company="字节跳动", title="Agent开发实习生 - 泛抖音",
                        city="杭州", job_type="intern", directions=["Agent"], duration_months=3, jd_text="做 Agent"),
                Posting(raw_item_id=ids[1], company_name="腾讯（IEG）", big_company="腾讯",
                        title="Coding Agent Research Intern", city="新加坡", job_type="intern",
                        posted_at=date(2026, 10, 9), jd_text="coding agent"),
                Posting(raw_item_id=ids[2], company_name="某某小科技", title="Agent 实习", city="杭州", job_type="intern")]
        post = Post(raw_item_id=ids[3], post_type="面经", companies=["滴滴"], big_companies=["滴滴"],
                    summary="滴滴后端一面，问 RAG", interview_rounds=1, interview_questions=["RAG 怎么召回"],
                    published_at=datetime(2026, 10, 10, tzinfo=UTC))
        s.add_all([*jobs, post])
        s.flush()
        return {"bd": jobs[0].id, "tx": jobs[1].id, "small": jobs[2].id, "post": post.id}


def test_jobs_search_filters_and_aliases(db):
    ids = seed()
    rt = ToolRuntime()
    out = rt.invoke(REGISTRY["jobs_search"], {"company": "TikTok", "city": "杭州"})
    assert f"J-{ids['bd']}" in out and "腾讯" not in out
    out = rt.invoke(REGISTRY["jobs_search"], {"city": "杭州"})
    assert f"J-{ids['small']}" not in out  # 默认只看大厂
    out = rt.invoke(REGISTRY["jobs_search"], {"city": "杭州", "big_only": False})
    assert f"J-{ids['small']}" in out
    out = rt.invoke(REGISTRY["jobs_search"], {"company": "美团"})
    assert '"returned": 0' in out and "如实告诉用户" in out


def test_jobs_get_and_bad_id(db):
    ids = seed()
    rt = ToolRuntime()
    assert "coding agent" in rt.invoke(REGISTRY["jobs_get"], {"job_id": f"J-{ids['tx']}", "response_format": "detailed"})
    bad = rt.invoke(REGISTRY["jobs_get"], {"job_id": "第二个"})
    assert bad.startswith("出错了") and "J-12" in bad


def test_posts_search_and_get(db):
    ids = seed()
    rt = ToolRuntime()
    out = rt.invoke(REGISTRY["posts_search"], {"company": "滴滴", "post_type": "面经"})
    assert f"P-{ids['post']}" in out
    assert "RAG 怎么召回" in rt.invoke(REGISTRY["posts_get"], {"post_id": f"P-{ids['post']}"})
