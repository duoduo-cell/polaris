"""学历门槛真实写法及反例：加分、替代条件、未知背景不能冒充明确冲突。"""
import pytest

from polaris.db.models import Job
from polaris.match import job_match
from polaris.proactive.decide import Profile


def fit(requirement, degree="本科在读"):
    j = Job(title="Agent开发实习生", job_type="intern", directions=["Agent"], requirements=[requirement])
    return job_match(j, Profile(roles=["Agent Engineering"], job_type="日常实习"),
                     background={"degree": degree} if degree else {})


@pytest.mark.parametrize("requirement", [
    "硕士研究生及以上学历在读，计算机相关专业背景",  # J-280 原文写法
    "相关专业硕士或以上学历，对该领域有深度了解",  # J-45
    "计算机、人工智能等相关专业硕士以上学历",  # J-3354
    "硕士学历在读", "硕士学位", "正在攻读计算机专业硕士",
    "Master's degree in Computer Science", "Masters degree required", "Master student in CS", "M.Sc. required",
    "硕士及以上学历，Python经验优先", "PhD required; Python preferred",
    "硕士及以上学历，论文不要求", "Master's degree required; prior internship optional",
])
def test_explicit_graduate_requirement_rejects_current_undergraduate(requirement):
    out = fit(requirement)
    assert out["eligibility"] == "incompatible" and out["score"] <= 59
    check = next(c for c in out["checks"] if c["key"] == "degree")
    assert check["ok"] is False and requirement in check["detail"]


@pytest.mark.parametrize("requirement", [
    "硕士及以上学历优先", "优先考虑硕士学历", "Master's degree preferred", "MSc is a plus",
    "本科及以上学历，硕士优先", "硕士学历，优秀本科生亦可", "Bachelor or Master degree",
    "不要求硕士学历", "硕士学位不是必须", "不限学历，硕士学历者欢迎申请",
    "Master's degree or equivalent practical experience", "Master Python and Go",
])
def test_preference_alternative_or_non_degree_word_is_not_hard_rejection(requirement):
    out = fit(requirement)
    assert out["eligibility"] == "uncertain"
    assert not any(c["key"] == "degree" and c["ok"] is False for c in out["checks"])


@pytest.mark.parametrize("degree", [None, "硕士在读", "博士在读"])
def test_missing_or_graduate_background_is_not_guessed_as_undergraduate(degree):
    out = fit("硕士研究生及以上学历在读", degree)
    assert out["eligibility"] == "uncertain"
    assert next(c for c in out["checks"] if c["key"] == "degree")["ok"] is None
