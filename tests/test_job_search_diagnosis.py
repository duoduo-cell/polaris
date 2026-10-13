"""空结果决策回归：范围为空、关键词漏召回、翻页越界和工具故障不能混同。"""
import json
from datetime import UTC, datetime

import pytest

from polaris.db.models import Job, Posting, RawItem
from polaris.db.session import session_scope
from polaris.tools import REGISTRY, jobs
from polaris.tools.runtime import ToolRuntime


def seed_job(*, city="北京", date_kind="published"):
    now = datetime.now(UTC)
    with session_scope() as s:
        raw = RawItem(source="manual", kind="job", raw_text="合成岗位", fetched_at=now,
                      raw_json={"publishDate" if date_kind == "published" else "LastUpdateTime": jobs.date.today().isoformat()},
                      content_hash=f"diagnosis-{city}-{date_kind}")
        j = Job(key=f"diagnosis-{city}-{date_kind}", title="Agent 开发实习", company_name="合成公司", city=city,
                cities=[city], directions=["Agent"], job_type="intern", status="active", is_synthetic=False,
                posted_at=jobs.date.today(), first_seen_at=now, last_seen_at=now)
        s.add_all([raw, j])
        s.flush()
        s.add(Posting(raw_item_id=raw.id, job_id=j.id, posted_at=j.posted_at))
        return j.id


def args(**kwargs):
    return jobs.JobsSearchArgs(city="北京", direction="Agent", job_type="intern", big_only=False, since_days=3, **kwargs)


@pytest.mark.parametrize("case", ["no_data", "other_city", "updated_only"])
def test_empty_scope_skips_retrieval_but_keeps_date_exclusion_evidence(db, monkeypatch, case):
    if case == "other_city":
        seed_job(city="上海")
    elif case == "updated_only":
        seed_job(date_kind="updated")
    monkeypatch.setattr("polaris.match.matches", lambda *a, **kw: {})

    def must_not_search(*a):
        pytest.fail("已无结构化候选，不应调用检索服务")

    monkeypatch.setattr(jobs, "ranked_ids", must_not_search)
    out = jobs.jobs_search(args(query="Agent 后端"))
    assert out["search"]["status"] == "scope_empty" and not out["search"]["query_executed"]
    assert out["search"]["candidates_before_query"] == 0
    assert out["scope"]["city"] == "北京" and out["scope"]["since_days"] == 3
    assert out["scope"]["excluded_unconfirmed_publication_dates"] == (case == "updated_only")
    if case == "updated_only":
        assert out["excluded_examples"][0]["date_kind"] == "updated"


def test_keyword_miss_can_recover_without_changing_filters(db, monkeypatch):
    jid = seed_job()
    monkeypatch.setattr(jobs, "ranked_ids", lambda *a: ([], "语义服务不可用，已退回关键词"))
    missing = jobs.jobs_search(args(query="LLM 应用工程"))
    assert missing["search"]["status"] == "query_empty"
    assert missing["search"]["candidates_before_query"] == 1
    assert missing["search_note"] == "语义服务不可用，已退回关键词"
    recovered = jobs.jobs_search(args())
    assert recovered["scope"] == missing["scope"]
    assert [j["id"] for j in recovered["items"]] == [f"J-{jid}"]


def test_scope_count_does_not_count_other_cities_and_preserves_ranking(db, monkeypatch):
    jid = seed_job()
    other = seed_job(city="上海")
    monkeypatch.setattr(jobs, "ranked_ids", lambda *a: ([other, jid], None))
    out = jobs.jobs_search(args(query="Agent"))
    assert out["search"]["candidates_before_query"] == 1
    assert out["search"]["retrieved_candidates"] == 1
    assert [j["id"] for j in out["items"]] == [f"J-{jid}"]


def test_exhausted_page_does_not_claim_empty_scope(db):
    seed_job()
    out = jobs.jobs_search(args(offset=1))
    assert not out["items"] and out["search"]["status"] == "page_exhausted"
    assert out["search"]["candidates_before_query"] == 1


def test_retrieval_error_stays_error_in_trace_not_empty_success(db, monkeypatch):
    seed_job()

    def fail(*a):
        raise TimeoutError("合成超时")

    monkeypatch.setattr(jobs, "ranked_ids", fail)
    rt = ToolRuntime(agent="main", record_db=False)
    rt.user_request = "最近三天的北京实习"
    out = rt.invoke(REGISTRY["jobs_search"], args(query="Agent").model_dump())
    assert not rt.calls[0]["ok"] and rt.calls[0]["error_kind"] == "internal"
    assert "出错了" in out and "scope_empty" not in out


def test_diagnosis_is_saved_with_actual_tool_result(db):
    from sqlalchemy import select

    from polaris.db.models import ToolCall

    rt = ToolRuntime(agent="main")
    rt.user_request = "最近三天的北京实习"
    out = json.loads(rt.invoke(REGISTRY["jobs_search"], args().model_dump()))
    with session_scope() as s:
        saved = s.scalar(select(ToolCall))
        assert saved.ok and json.loads(saved.output)["search"] == out["search"]
        assert saved.args["city"] == "北京" and saved.args["since_days"] == 3
