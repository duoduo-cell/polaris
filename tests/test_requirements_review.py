"""门槛解释保留原句、个人事实与未确认项；不能把优先项当硬要求。"""
from datetime import UTC, date, datetime

import pytest

from polaris.db.models import Job
from polaris.match import job_match
from polaris.proactive.decide import Profile


def review(reqs, *, background=None, duration=None, need_months=6):
    job = Job(title="合成Agent实习", requirements=reqs, duration_months=need_months, directions=["Agent"])
    return job_match(job, Profile(roles=["Agent"], duration_months=duration), background=background)


def item(result, key):
    return next(x for x in result["requirements_review"] if x["key"] == key)


def test_undergraduate_level_and_optional_papers_are_explained_without_full_qualification_claim():
    reqs = ["本科及以上学历在读，计算机或相关专业", "有相关论文或优秀项目者优先，但论文不是必要条件"]
    result = review(reqs, background={"degree": "本科在读", "paper_experience": "无论文"})
    degree, paper, duration = (item(result, key) for key in ("degree", "paper", "months"))
    assert degree["status"] == "level_compatible" and degree["personal"] == "本科在读"
    assert degree["requirements"] == reqs[:1] and "专业" in degree["note"]
    assert paper["status"] == "not_required" and paper["personal"] == "无论文"
    assert paper["requirements"] == reqs[1:] and "不构成论文硬冲突" in paper["note"]
    assert duration["status"] == "unknown" and duration["personal"] is None
    assert "6个月" in duration["note"] and "未记录" in duration["note"]
    assert result["eligibility"] == "uncertain"


def test_missing_personal_facts_are_unknown_and_not_rejections():
    result = review(["本科及以上在读", "至少发表一篇论文"])
    assert item(result, "degree")["status"] == "unknown"
    assert item(result, "paper")["status"] == "unknown"
    assert result["eligibility"] == "uncertain"


@pytest.mark.parametrize("text", [
    "每周至少4天，论文不是必要条件", "至少实习六个月，优秀论文优先", "At least four days a week; papers preferred.",
])
def test_attendance_minimum_cannot_be_misread_as_mandatory_paper(text):
    result = review([text], background={"paper_experience": "无论文"})
    assert result["eligibility"] != "incompatible"
    assert not any(c["key"] == "paper" and c["ok"] is False for c in result["checks"])


def test_preferred_project_does_not_erase_separate_mandatory_paper_clause():
    result = review(["至少发表过一篇论文；有优秀工程项目者优先"], background={"paper_experience": "无论文"})
    assert result["eligibility"] == "incompatible"
    assert item(result, "paper")["status"] == "conflict"
    assert any(c["key"] == "paper" and c["ok"] is False for c in result["checks"])


def test_explicit_paper_alternative_and_conflicting_jd_remain_unconfirmed():
    alternative = review(["至少发表过一篇论文，或有优秀工程项目经历"], background={"paper_experience": "无论文"})
    assert alternative["eligibility"] != "incompatible"
    assert item(alternative, "paper")["status"] == "unknown"
    contradiction = review(["至少发表一篇论文", "论文不是必要条件"], background={"paper_experience": "无论文"})
    assert item(contradiction, "paper")["status"] == "conflicting_jd"
    assert contradiction["eligibility"] != "incompatible"


def test_graduate_gate_and_missing_months_are_explained_separately():
    result = review(["正在攻读博士", "至少发表一篇论文"], background={"degree": "本科在读", "paper_experience": "无论文"})
    assert item(result, "degree")["status"] == "conflict"
    assert item(result, "paper")["status"] == "conflict"
    assert item(result, "months")["status"] == "unknown"


@pytest.mark.parametrize("personal,status", [(3, "conflict"), (6, "meets_recorded_minimum"), (9, "meets_recorded_minimum")])
def test_duration_is_compared_only_when_both_values_are_recorded(personal, status):
    result = review([], duration=personal)
    assert item(result, "months")["status"] == status
    unknown = review([], duration=personal, need_months=None)
    assert item(unknown, "months")["status"] == "unknown"


def test_degree_does_not_claim_major_school_or_competition_equivalence():
    result = review(["本科及以上学历，计算机相关专业；ACM竞赛优先"], background={"degree": "本科在读"})
    degree = item(result, "degree")
    assert degree["status"] == "level_compatible"
    assert "专业" in degree["note"] and "不能" in degree["note"]


def test_reviews_do_not_add_positive_qualification_checks_to_score():
    p = Profile(roles=["Agent"], skills=["Python"])
    one = Job(title="合成Agent实习", requirements=["本科及以上在读", "论文不是必要条件"], directions=["Agent"])
    left = job_match(one, p, background={"degree": "本科在读", "paper_experience": "无论文"})
    right = job_match(one, p)
    assert left["score"] == right["score"]
    assert not any(c["key"] in ("degree", "paper") for c in left["checks"])


def test_matching_memory_date_is_beijing_and_review_references_actual_fact(db, monkeypatch):
    from polaris import match
    from polaris.db.session import session_scope
    from polaris.memory.update import Candidate, Context, apply
    from polaris.proactive import daily

    night = datetime(2026, 10, 22, 17, tzinfo=UTC)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return night.astimezone(tz) if tz else night.replace(tzinfo=None)

    monkeypatch.setattr(match, "datetime", Clock)
    with session_scope() as s:
        apply(Context(s, night, "合成测试"), Candidate("degree", "本科在读", "合成本科在读", valid_from=date(2026, 10, 23)))
        apply(Context(s, night, "合成测试"), Candidate("duration_months", "6", "合成可实习六个月", valid_from=date(2026, 10, 23)))
    with session_scope() as s:
        j = Job(key="review-date", title="合成Agent实习", requirements=["本科及以上在读"], duration_months=6,
                first_seen_at=night, last_seen_at=night)
        s.add(j)
        s.flush()
        result = match.matches(s, [j.id], criteria={"direction": "Agent"})[j.id]
        assert item(result, "degree")["personal"] == "本科在读"
        assert item(result, "degree")["personal_source"].startswith("M-")
        assert item(result, "months")["personal"] == 6
    assert daily.profile_from_memory(night).duration_months == 6
