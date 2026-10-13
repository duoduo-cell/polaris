"""真实失败的合成回归：博士论文门槛不能因Agent方向吻合而被评为可推荐。"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from polaris.db.models import Job
from polaris.match import job_match
from polaris.proactive.decide import Profile
from polaris.sources import date_kind


def job(requirements):
    return Job(title="Agent研究实习", company="腾讯", cities=["伦敦"], city="伦敦", job_type="intern",
               directions=["Agent"], requirements=requirements, jd_text="合成要求")


def test_undergraduate_and_no_papers_conflict_with_mandatory_requirements():
    j = job(["正在攻读博士，或完成硕士并已获得博士录取。", "至少发表过一篇论文。"])
    out = job_match(j, Profile(roles=["Agent"], job_type="日常实习", skills=["Python"]),
                    background={"degree": "本科在读", "paper_experience": "无论文"})
    assert out["eligibility"] == "incompatible" and out["level"] == "weak"
    assert {c["key"] for c in out["checks"] if c["ok"] is False} == {"degree", "paper"}


def test_unknown_background_is_not_rejection_and_preferred_paper_is_not_mandatory():
    j = job(["博士优先，优秀本科生亦可。", "有论文优先。"])
    out = job_match(j, Profile(roles=["Agent"]), background={"degree": "本科在读", "paper_experience": "无论文"})
    assert out["eligibility"] == "uncertain"
    assert not any(c["ok"] is False for c in out["checks"])
    out = job_match(job(["正在攻读PhD。", "At least one publication in a leading ML conference."]), Profile(roles=["Agent"]))
    assert out["eligibility"] == "uncertain" and all(c["ok"] is None for c in out["checks"] if c["key"] in ("degree", "paper"))


@pytest.mark.parametrize("body,raw_json,kind", [
    ("岗位\n更新时间：2026年10月12日", {"LastUpdateTime": "2026-10-12"}, "updated"),
    ("岗位\n发布日期：2026-10-12", {}, "published"),
    ("岗位\n今天刚采到", {}, "unknown"),
])
def test_date_kind_keeps_update_and_fetch_separate(body, raw_json, kind):
    raw = SimpleNamespace(source="tencent:careers", raw_text=body, raw_json=raw_json, published_at=None)
    assert date_kind(raw) == kind


def test_recent_search_excludes_updates_future_and_oldest_fourth_day_before_pagination(db, monkeypatch):
    from polaris.db.models import Posting, RawItem
    from polaris.db.session import session_scope
    from polaris.tools import jobs

    now = datetime.now(UTC)
    with session_scope() as s:
        for i, (offset, kind) in enumerate([(0, "updated"), (0, "published"), (-1, "published"), (3, "published")]):
            date = jobs.date.today() - timedelta(days=offset)
            raw = RawItem(source="tencent:careers", kind="job", raw_text="合成岗位", raw_json={
                "LastUpdateTime" if kind == "updated" else "publishDate": date.isoformat()}, fetched_at=now,
                content_hash=f"date-kind-{i}")
            s.add(raw)
            j = job([])
            j.key = f"date-kind-{i}"
            j.posted_at, j.status, j.first_seen_at, j.last_seen_at = date, "active", now, now
            j.is_synthetic = False
            s.add(j)
            s.flush()
            s.add(Posting(raw_item_id=raw.id, job_id=j.id, posted_at=date))
    monkeypatch.setattr("polaris.match.matches", lambda *args, **kwargs: {})
    r = jobs.jobs_search(jobs.JobsSearchArgs(since_days=3, big_only=False, limit=1))
    assert [x["id"] for x in r["items"]] == ["J-2"]
    assert r["scope"]["excluded_unconfirmed_publication_dates"] == 1 and r["next_offset"] is None
    assert "可能是发布或更新" in r["library"]["dated"]
    r = jobs.jobs_search(jobs.JobsSearchArgs(since_days=3, big_only=False, date_basis="reported"))
    assert [x["posted_at_kind"] for x in r["items"]] == ["updated", "published"]
