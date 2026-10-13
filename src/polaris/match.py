"""一个岗位和你的条件对得上多少：给网页的岗位卡片和详情用，确定性打分，不调模型。

逐条列出：硬条件（城市、类型、每周天数、实习时长、不想去的公司）、方向、技能（你已有 / 你还缺）。
分数只是这些条目的加权汇总，条目本身才是重点。岗位没写的条件记为"没写"，不算对也不算错。
你的条件来自记忆卡片（和主动推送用的是同一份，proactive/daily.profile_from_memory）；卡片里什么都没有时不打分。
"""
import re
from datetime import UTC, datetime
from typing import Any

from polaris.align.normalize import cities as split_cities
from polaris.companies import match as big_company
from polaris.db.models import Job
from polaris.proactive.decide import JOB_TYPE_NAMES, JOB_TYPE_WORDS, Item, Profile, role_hit
from polaris.vocab import skills_in

UNKNOWN = 0.6  # 岗位没写的条件按"大概率没问题"算，但比确定对得上低


def profile(now: datetime | None = None) -> Profile | None:
    from polaris.proactive.daily import profile_from_memory

    p = profile_from_memory(now or datetime.now(UTC))
    if not (p.roles or p.cities or p.job_type or p.days_per_week or p.duration_months or p.skills or p.companies_avoid):
        return None
    return p


def _company(name: str | None) -> str | None:
    if not name:
        return None
    big = big_company(name)
    return big.name if big else name.strip()


def _check(key: str, label: str, ok: bool | None, detail: str) -> dict[str, Any]:
    return {"key": key, "label": label, "ok": ok, "detail": detail}


def job_match(job: Job, p: Profile, *, background: dict[str, str] | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    background = background or {}
    reqs = [str(r) for r in (job.requirements or [])]
    graduate_only = next((r for r in reqs if re.search(r"博士|硕士及以上|Ph\.?D|doctor(?:al|ate)", r, re.I)
                          and not re.search(r"本科|bachelor|undergrad|优先|preferred|preferably", r, re.I)), None)
    paper_required = next((r for r in reqs if re.search(r"至少[^。\n]{0,40}(?:一篇|1\s*篇|论文)|"
                                                       r"at least\s+(?:one|1)[^.;\n]{0,60}(?:publication|paper)", r, re.I)
                           and not re.search(r"优先|preferred|preferably|加分", r, re.I)), None)
    degree = background.get("degree")
    if graduate_only:
        undergrad = bool(degree and re.search(r"本科|bachelor|undergrad", degree, re.I))
        # 只识别明确矛盾；未知学历、硕博各自是否达标不由这个保守规则猜。
        checks.append(_check("degree", "学历", False if undergrad else None,
                             f"岗位要求：{graduate_only}；你目前：{degree or '未记录'}"))
    if paper_required:
        papers = background.get("paper_experience")
        explicit_none = papers in ("无论文", "没有论文")
        checks.append(_check("paper", "论文", False if explicit_none else None,
                             f"岗位要求：{paper_required}；你的论文情况：{papers or '未记录'}"))
    company = _company(job.company or job.company_name)
    if p.companies_avoid and company and company in {_company(c) for c in p.companies_avoid}:
        checks.append(_check("avoid", "公司", False, f"你说过不想去{company}"))
    elif p.companies_like and company and company in {_company(c) for c in p.companies_like}:
        checks.append(_check("like", "公司", True, f"是你想去的{company}"))
    if p.cities:
        have = list(job.cities or []) or sorted(split_cities(job.city))
        want = {c.removesuffix("市") for c in p.cities}
        hit = [c for c in have if c in want]
        checks.append(_check("city", "城市", None, "岗位没写城市") if not have else
                      _check("city", "城市", bool(hit), "、".join(hit) if hit else f"在{'、'.join(have[:3])}"))
    if p.job_type:
        want_type = JOB_TYPE_WORDS.get(p.job_type, p.job_type)
        name = JOB_TYPE_NAMES.get(job.job_type or "", job.job_type)
        checks.append(_check("type", "类型", None, "岗位没写类型") if not job.job_type else
                      _check("type", "类型", want_type == job.job_type,
                             name if want_type == job.job_type else f"是{name}，你找{p.job_type}"))
    if p.days_per_week:
        need = job.days_per_week
        checks.append(_check("days", "每周天数", None, "没写每周几天") if not need else
                      _check("days", "每周天数", need <= p.days_per_week,
                             f"每周 {need} 天" if need <= p.days_per_week else f"要每周 {need} 天，你能去 {p.days_per_week} 天"))
    if p.duration_months:
        need = job.duration_months
        checks.append(_check("months", "实习时长", None, "没写实习多久") if not need else
                      _check("months", "实习时长", need <= p.duration_months,
                             f"{need} 个月" if need <= p.duration_months else f"要 {need} 个月，你能实习 {p.duration_months} 个月"))
    role = None
    if p.roles:
        reqs = " ".join(map(str, job.requirements or []))[:800]
        item = Item(key="", kind="job", title=job.title or "", company=job.company, directions=list(job.directions or []),
                    text=reqs)
        strength = {r: role_hit(r, item) for r in p.roles}
        hit = [r for r, v in strength.items() if v > 0]
        role = max(strength.values())
        checks.append(_check("role", "方向", bool(hit), "、".join(hit) if hit else f"不是你找的{'、'.join(p.roles[:2])}"))

    required = skills_in(" ".join([job.title or "", *map(str, job.requirements or [])]) or job.jd_text)
    mine_text = " ".join(p.skills)
    mine = set(skills_in(mine_text)) | {s for s in required if s.lower() in mine_text.lower()}
    have = [s for s in required if s in mine]
    missing = [s for s in required if s not in mine] if p.skills else []

    def value(c: dict[str, Any]) -> float:
        return 1.0 if c["ok"] else UNKNOWN if c["ok"] is None else 0.0

    hard = [c for c in checks if c["key"] in ("avoid", "city", "type", "days", "months", "degree", "paper")]
    hard_score = sum(map(value, hard)) / len(hard) if hard else UNKNOWN
    role_score = role if role is not None else UNKNOWN
    skill_score = len(have) / len(required) if p.skills and required else UNKNOWN
    score = round(100 * (0.5 * hard_score + 0.25 * role_score + 0.25 * skill_score))
    if any(c["ok"] is False for c in hard) or role == 0:
        score = min(score, 59)  # 有硬条件明确对不上，分数再高也不算合适
    eligibility = "incompatible" if any(c["ok"] is False for c in hard) else "uncertain"
    return {"score": score, "level": "good" if score >= 80 else "ok" if score >= 60 else "weak", "checks": checks,
            "eligibility": eligibility, "note": "已知硬门槛不符就不推荐；未记录的技能不代表不会，分数不是录用概率。",
            "skills": {"required": required, "have": have, "missing": missing,
                       "not_stated": [s for s in required if s not in mine], "known": bool(p.skills)}}


def matches(s: Any, job_ids: list[int], p: Profile | None = None) -> dict[int, dict[str, Any]]:
    """一批岗位各自的匹配；记忆里还没有条件时返回空。"""
    from sqlalchemy import select

    personalized = p is None
    p = p or profile()
    if p is None or not job_ids:
        return {}
    from polaris.memory.update import card

    background = {f.attribute: f.value for f in card(s, datetime.now(UTC).date())
                  if f.attribute in ("degree", "paper_experience")} if personalized else {}
    return {j.id: job_match(j, p, background=background) for j in s.scalars(select(Job).where(Job.id.in_(job_ids)))}
