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
from polaris.report import BEIJING
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


GRADUATE = re.compile(r"博士|Ph\.?D|doctor(?:al|ate)|"
                      r"硕士(?:研究生)?(?:(?:及|或)?以上|学历|学位|在读)|"
                      r"(?:攻读|就读)[^，,。;；\n]{0,12}硕士|"
                      r"\bmaster(?:['’]s|s)?\s+(?:degree|student|candidate|program)|\bM\.?Sc\.?\b", re.I)
PREFERRED = re.compile(r"优先|preferred|preferably|加分|a plus|nice.to.have", re.I)
DEGREE_ALTERNATIVE = re.compile(r"学历不限|不限学历|不限制学历|同等(?:实践|工作)?经验|"
                                r"equivalent (?:practical |work |professional )?experience", re.I)
OPTIONAL_DEGREE = re.compile(r"不要求|无需|不是必须|不强制|not required|not mandatory|optional", re.I)
PAPER_WORD = re.compile(r"论文|\bpaper\b|\bpapers\b|publication", re.I)
PAPER_MINIMUM = re.compile(
    r"(?:至少|最少|不少于)\s*(?:发表(?:过)?|有|拥有)?\s*[一两二三四五六七八九十\d]+\s*篇[^，,。;；\n]{0,40}论文|"
    r"(?:必须|须|需|要求)\s*(?:已经|已)?\s*(?:发表(?:过)?|拥有|具备|有)[^，,。;；\n]{0,25}论文|"
    r"\bat least\s+(?:one|two|[1-9]\d*)\s+(?:[\w-]+\s+){0,6}(?:papers?|publications?)\b", re.I)
PAPER_OPTIONAL = re.compile(
    r"论文\s*(?:不是必要条件|不是必需条件|不(?:是)?必要|非必要|不作硬性要求)|"
    r"(?:不要求|无需)\s*(?:有|发表)?\s*论文|\b(?:papers?|publications?)\s+(?:are\s+)?(?:not required|optional)\b", re.I)


def _paper_terms(requirements: list[str]) -> tuple[list[str], list[str], list[str]]:
    """只认明确论文数量要求，保留原句；出勤的“至少”和别的优先项不跨分句传播。"""
    required, optional, preferred = [], [], []
    for original in requirements:
        if not PAPER_WORD.search(original):
            continue
        if PAPER_OPTIONAL.search(original):
            optional.append(original)
        for sentence in re.split(r"[。;；\n]", original):
            # 替代资格不只比较论文数量；保守留待核实，不断言整项不符。
            alternative = re.search(r"或(?:者)?\s*(?:有|具备|拥有|优秀|丰富|同等)|\bor\b", sentence, re.I)
            for clause in re.split(r"[，,]", sentence):
                if not PAPER_WORD.search(clause):
                    continue
                if PREFERRED.search(clause):
                    if original not in preferred:
                        preferred.append(original)
                elif PAPER_MINIMUM.search(clause) and not alternative and original not in required:
                    required.append(original)
    return required, optional, preferred


def _graduate_requirement(requirements: list[str]) -> str | None:
    for original in requirements:
        # 明确接受本科或经验替代时，不把研究生字样当作本科排除条件。
        if re.search(r"本科|bachelor|undergrad", original, re.I) or DEGREE_ALTERNATIVE.search(original):
            continue
        for clause in re.split(r"[，,。;；\n]", original):
            if GRADUATE.search(clause) and not PREFERRED.search(clause) and not OPTIONAL_DEGREE.search(clause):
                return original
    return None


def _requirements_review(job: Job, p: Profile, background: dict[str, str],
                         references: dict[str, str]) -> list[dict[str, Any]]:
    reqs = [str(r) for r in (job.requirements or [])]
    degree_quotes = [r for r in reqs if re.search(r"本科|硕士|博士|学历|bachelor|undergrad|master|ph\.?d|doctor", r, re.I)]
    degree = background.get("degree")
    undergrad = bool(degree and re.search(r"本科|bachelor|undergrad", degree, re.I))
    graduate_only = _graduate_requirement(reqs)
    accepts_undergrad = any(re.search(r"本科(?:及|或)?以上|本科生亦可|bachelor|undergrad", r, re.I) for r in degree_quotes)
    degree_status = "conflict" if graduate_only and undergrad else "level_compatible" if accepts_undergrad and undergrad else "unknown"
    degree_note = ("已记录的本科与研究生硬门槛明确冲突。" if degree_status == "conflict" else
                   "学历层级可对应；不能据此确认专业、在读状态或其他附加条件全部满足。" if degree_status == "level_compatible" else
                   "学历条件或个人情况尚未确定，按原句核对，不自动判符合或不符。")
    mandatory, optional, preferred = _paper_terms(reqs)
    papers = background.get("paper_experience")
    paper_status = ("conflicting_jd" if mandatory and optional else "not_required" if optional else
                    "conflict" if mandatory and papers in ("无论文", "没有论文") else
                    "preferred" if preferred and not mandatory else "unknown")
    paper_note = {"conflicting_jd": "原文同时出现必须与非必要说法，需核实；不能直接判符合或硬冲突。",
                  "not_required": "原文明示论文非必要；没有论文本身不构成论文硬冲突。",
                  "conflict": "原文明示论文数量要求，与你明确记录的无论文冲突。",
                  "preferred": "原文把论文列为优先项，不能仅因无论文判硬冲突。",
                  "unknown": "论文是否满足数量、发表状态或领域等条件未确认；未记录不等于没有。"}[paper_status]
    need, available = job.duration_months, p.duration_months
    month_status = "unknown" if need is None or available is None else "conflict" if available < need else "meets_recorded_minimum"
    month_note = (f"岗位实习时长字段：{need}个月；" if need is not None else "岗位未写实习时长；")
    month_note += (f"个人已记录可实习{available}个月。" if available is not None else "个人可实习时长未记录，待核实。")
    return [
        {"key": "degree", "label": "学历", "status": degree_status, "requirements": degree_quotes,
         "personal": degree, "personal_source": references.get("degree"), "note": degree_note},
        {"key": "paper", "label": "论文", "status": paper_status, "requirements": [r for r in reqs if PAPER_WORD.search(r)],
         "personal": papers, "personal_source": references.get("paper_experience"), "note": paper_note},
        {"key": "months", "label": "实习时长", "status": month_status,
         "requirements": [r for r in reqs if re.search(r"(?:个)?月|months?", r, re.I)],
         "personal": available, "personal_source": references.get("duration_months"), "note": month_note},
    ]


def job_match(job: Job, p: Profile, *, background: dict[str, str] | None = None,
              background_refs: dict[str, str] | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    background = background or {}
    reqs = [str(r) for r in (job.requirements or [])]
    graduate_only = _graduate_requirement(reqs)
    mandatory, optional, _ = _paper_terms(reqs)
    paper_required = mandatory[0] if mandatory and not optional else None
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
            "requirements_review": _requirements_review(job, p, background, background_refs or {}),
            "eligibility": eligibility, "note": "已知硬门槛不符就不推荐；未记录的技能不代表不会，分数不是录用概率。",
            "skills": {"required": required, "have": have, "missing": missing,
                       "not_stated": [s for s in required if s not in mine], "known": bool(p.skills)}}


def matches(s: Any, job_ids: list[int], p: Profile | None = None, *,
            criteria: dict[str, Any] | None = None) -> dict[int, dict[str, Any]]:
    """一批岗位各自的匹配；记忆里还没有条件时返回空。"""
    from sqlalchemy import select

    personalized = p is None
    from polaris.matching_scope import criteria as clean_criteria
    from polaris.matching_scope import scope_note, scoped_profile

    scope = clean_criteria(criteria or {})
    now = datetime.now(UTC)
    p = scoped_profile(p or profile(now), scope)
    if p is None or not job_ids:
        return {}
    from polaris.memory.update import card

    facts = [f for f in card(s, now.astimezone(BEIJING).date())
             if f.attribute in ("degree", "paper_experience", "duration_months")] if personalized else []
    background = {f.attribute: f.value for f in facts if f.attribute != "duration_months"}
    refs = {f.attribute: f"M-{f.id}" for f in facts}
    results = {j.id: job_match(j, p, background=background, background_refs=refs)
               for j in s.scalars(select(Job).where(Job.id.in_(job_ids)))}
    if scope:
        for result in results.values():
            result["criteria"] = scope.copy()
            result["note"] = scope_note(scope) + result["note"]
    return results
