"""按 HR 的眼光看你的情况，并和同类求职者的公开自述横向对照（docs/design/WATCH.md 第 6 节）。

HR 视角拆成三个问题：明确的资格你满不满足；你的经历能不能支撑岗位要求；面试官能追问到哪些真实工作。
全部由规则从岗位、记忆卡片和样本库算出来，不调模型；对话里由主 Agent 根据这些事实解释。

同类样本只用帖子里作者自述的公开内容，按可比程度分三层；不爬个人主页，不从学校名推测其他信息。
学校只分“写了 985/211 类”“双非/非 92 类”“海外”“写了但没法归类”“没写”；你自己的学校只认 39 所 985 名单，名单外标“未归类”。
"""
import re
import time
from collections import Counter
from datetime import UTC, datetime
from statistics import median
from typing import Any

from sqlalchemy.orm import Session

from polaris.config import get_settings
from polaris.db.models import Job

SCHOOLS_985 = (
    "北京大学", "清华大学", "中国人民大学", "北京航空航天大学", "北京理工大学", "中国农业大学", "北京师范大学", "中央民族大学",
    "南开大学", "天津大学", "大连理工大学", "东北大学", "吉林大学", "哈尔滨工业大学", "复旦大学", "同济大学", "上海交通大学",
    "华东师范大学", "南京大学", "东南大学", "浙江大学", "中国科学技术大学", "厦门大学", "山东大学", "中国海洋大学", "武汉大学",
    "华中科技大学", "湖南大学", "中南大学", "中山大学", "华南理工大学", "四川大学", "电子科技大学", "重庆大学", "西安交通大学",
    "西北工业大学", "西北农林科技大学", "兰州大学", "国防科技大学",
)
SHORT_985 = ("北大", "清华", "人大", "北航", "北理", "北师大", "南开", "天大", "大工", "吉大", "哈工大", "复旦", "同济", "上交",
             "华东师大", "南大", "浙大", "中科大", "厦大", "山大", "武大", "华科", "湖大", "中南", "中大", "华南理工", "华工",
             "川大", "电子科大", "成电", "重大", "西交", "西工大", "西农", "兰大", "国防科大")
TOP_WORDS = re.compile(r"985|211|C9|清北|(?<!非)(?<!双非)9\d?2|(?<![非\d])[29][本硕]")  # “2本9硕”：211 本、985 硕
LOWER_WORDS = re.compile(r"双非|非\s*92|非\s*985|非\s*211|二本|三本|普本|民办")
ABROAD_WORDS = re.compile(r"海外|留学|QS|美国|英国|澳洲|澳大利亚|新加坡|香港|港[校大]|日本|加拿大")
SCHOOL_BUCKETS = ("写了 985/211 类", "写了双非/非 92 类", "海外", "写了但没法归类", "没写")


def school_bucket(text: str | None) -> str:
    """帖子里自述的学校说法归类；“2本9硕”这种本硕不同的按写到的最高一档算，只作粗分。"""
    if not text or not text.strip() or text.strip() in ("未说明", "无"):
        return "没写"
    if TOP_WORDS.search(text) or any(n in text for n in SCHOOLS_985) or any(n in text for n in SHORT_985):
        return "写了 985/211 类"
    if LOWER_WORDS.search(text):
        return "写了双非/非 92 类"
    if ABROAD_WORDS.search(text):
        return "海外"
    return "写了但没法归类"


def my_school_bucket(text: str | None) -> str:
    if not text:
        return "没写"
    if any(n in text for n in SCHOOLS_985) or any(n in text for n in SHORT_985) or re.search(r"985", text):
        return "写了 985/211 类"
    return school_bucket(text) if school_bucket(text) != "写了但没法归类" else "未归类（只认 985 名单）"


# ---- 范围内岗位和你的条件对得上多少（态势报告和 HR 视角共用） ----

def fit_summary(s: Session, jobs: list[Job], criteria: dict[str, Any]) -> dict[str, Any]:
    from polaris.match import matches
    from polaris.matching_scope import criteria as clean_criteria

    fits = matches(s, [j.id for j in jobs], criteria=clean_criteria(criteria)) if jobs else {}
    gates: Counter = Counter()
    unknown: Counter = Counter()
    gate_examples: dict[str, list[str]] = {}
    open_ok, open_refs = 0, []
    skills_required: Counter = Counter()
    have: set[str] = set()
    for j in jobs:
        fit = fits.get(j.id)
        if not fit:
            continue
        # 同一个岗位的同一种门槛只算一次：逐条核对和门槛解释可能都指向学历
        blocking = {c["label"] for c in fit.get("checks", []) if c.get("ok") is False}
        blocking |= {r["label"] for r in fit.get("requirements_review", []) if r.get("status") == "conflict"}
        unknown.update({c["label"] for c in fit.get("checks", []) if c.get("ok") is None} - blocking)
        for label in blocking:
            gates[label] += 1
            gate_examples.setdefault(label, []).append(f"J-{j.id}")
        if not blocking and fit.get("eligibility") != "incompatible":
            open_ok += 1
            open_refs.append(f"J-{j.id}")
        sk = fit.get("skills") or {}
        skills_required.update(set(sk.get("required") or []))
        have.update(sk.get("have") or [])
    total = len(jobs)
    return {
        "jobs": total, "with_match": len(fits), "no_known_conflict": open_ok, "no_conflict_examples": open_refs[:5],
        "hard_gates": dict(gates.most_common(8)), "gate_examples": {k: v[:3] for k, v in gate_examples.items()},
        "unknown_checks": dict(unknown.most_common(6)),
        "skills": [{"skill": k, "jobs": n, "share": round(n / total, 2) if total else None, "in_your_card": k in have}
                   for k, n in skills_required.most_common(12)],
    }


# ---- 经历证据：GitHub 公开仓库（只读） ----

GITHUB = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
_GITHUB_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
GITHUB_TTL = 3600


def github_evidence(text: str, fetch: Any = None) -> dict[str, Any] | None:
    """项目里写了 GitHub 链接时，读公开仓库的基本信息：最近提交、主要语言、有没有 README 和测试目录。读不到就说读不到。"""
    m = GITHUB.search(text or "")
    if not m:
        return None
    owner, repo = m.group(1), m.group(2).removesuffix(".git")
    key = f"{owner}/{repo}"
    cached = _GITHUB_CACHE.get(key)
    if cached and time.time() - cached[0] < GITHUB_TTL:
        return cached[1]
    if fetch is None:
        import httpx

        def fetch(url: str) -> Any:
            r = httpx.get(url, timeout=8, headers={"Accept": "application/vnd.github+json"})
            r.raise_for_status()
            return r.json()
    try:
        meta = fetch(f"https://api.github.com/repos/{key}")
        files = fetch(f"https://api.github.com/repos/{key}/contents")
        names = {f.get("name", "").lower() for f in files} if isinstance(files, list) else set()
        out = {"repo": key, "url": meta.get("html_url"), "description": meta.get("description"), "language": meta.get("language"),
               "stars": meta.get("stargazers_count"), "pushed_at": meta.get("pushed_at"), "archived": meta.get("archived"),
               "has_readme": any(n.startswith("readme") for n in names),
               "has_tests": bool(names & {"tests", "test", "__tests__", "spec"}), "ok": True}
    except Exception as e:  # noqa: BLE001  读不到不影响别的结论
        out = {"repo": key, "ok": False, "error": f"读不到：{type(e).__name__}"}
    _GITHUB_CACHE[key] = (time.time(), out)
    return out


# ---- HR 视角 ----

FOLLOW_UP_FIELDS = {
    "days_per_week": "每周能到岗几天（很多岗位卡这个）",
    "duration_months": "能实习多久",
    "available_from": "最早什么时候能开始",
    "internship_experience": "实习经历（没有也请明确说，HR 会问）",
    "paper_experience": "论文情况（没有也请明确说）",
    "project": "做过的项目（本人负责哪部分、结果怎样）",
}


def review(s: Session, criteria: dict[str, Any] | None = None, job_id: int | None = None,
           fetch_github: Any = None) -> dict[str, Any]:
    from polaris.memory.schema import ATTRIBUTES
    from polaris.memory.update import card
    from polaris.watch import describe, normalize_criteria, scope_query

    criteria = normalize_criteria(criteria or {})
    if job_id is not None:
        job = s.get(Job, job_id)
        if job is None:
            raise LookupError(f"没有 J-{job_id}")
        jobs, scope_text = [job], f"岗位 J-{job_id}"
    else:
        jobs = list(s.scalars(scope_query(criteria).where(Job.status == "active")
                              .order_by(Job.first_seen_at.desc().nulls_last(), Job.id).limit(300)))
        scope_text = describe(criteria)
    facts = card(s)
    by: dict[str, list[Any]] = {}
    for f in facts:
        by.setdefault(f.attribute, []).append(f)
    fit = fit_summary(s, jobs, criteria)
    projects = [{"ref": f"M-{f.id}", "text": f.value, "github": github_evidence(f.value, fetch_github)} for f in by.get("project", [])]
    missing = [{"field": k, "why": v} for k, v in FOLLOW_UP_FIELDS.items() if k not in by]
    return {
        "scope": scope_text,
        "qualification": {
            "summary": f"范围内 {fit['jobs']} 个在招岗位，没有已知硬冲突的 {fit['no_known_conflict']} 个",
            "hard_gates": fit["hard_gates"], "gate_examples": fit["gate_examples"], "unknown_checks": fit["unknown_checks"],
            "no_conflict_examples": fit["no_conflict_examples"]},
        "evidence": {
            "skills_demand": fit["skills"],
            "your_skills": [f.value for f in by.get("skill", [])],
            "projects": projects,
            "internships": [f.value for f in by.get("internship_experience", [])],
            "papers": [f.value for f in by.get("paper_experience", [])],
        },
        "you": {ATTRIBUTES[k].label: [f.value for f in v] for k, v in by.items()
                if k in ("school", "degree", "major", "grad_year", "days_per_week", "duration_months", "available_from")},
        "ask_user": missing,
        "note": ("HR 视角三问：资格（硬门槛）、证据（经历能否支撑 JD 要求）、可追问（面试会问到的真实工作）。"
                 "技能“没写”只表示卡片里没记，不等于不会；不要替用户编项目细节或数字，没写清的列为要问用户的。"),
    }


# ---- 同类样本横向对照 ----

def _tier_stats(people: list[Any]) -> dict[str, Any]:
    from polaris.tools.peers import NO_INTERN

    known_intern = [len(p.internships) for p in people if p.internships]
    known_intern += [0] * sum(1 for p in people if not p.internships and NO_INTERN.search(p.evidence or ""))
    return {
        "people": len(people),
        "outcome": dict(Counter(p.outcome for p in people)),
        "stage": dict(Counter(p.stage for p in people)),
        "school": {b: sum(1 for p in people if school_bucket(p.school) == b) for b in SCHOOL_BUCKETS},
        "papers_stated": sum(1 for p in people if p.papers or p.paper_summary),
        "top_papers": sum(1 for p in people if p.top_papers),
        "internships_known": len(known_intern),
        "internships_median": median(known_intern) if known_intern else None,
    }


def compare(s: Session, *, company: str | None, direction: str | None, job_type: str | None = "实习",
            since_days: int = 180) -> dict[str, Any]:
    """三层可比程度：同公司同方向同类型 → 同方向同类型（各大厂）→ 同方向（不限类型）。每层分“全部”和“拿到 offer 的”。"""
    from polaris.memory.update import card
    from polaris.tools.peers import PeersSearchArgs, select_people

    now = datetime.now(UTC)
    layers = []
    specs = [("同公司、同方向、同类型", {"company": company, "direction": direction, "job_type": job_type}),
             ("同方向、同类型（各大厂）", {"direction": direction, "job_type": job_type}),
             ("同方向（不限类型）", {"direction": direction})]
    seen_specs = set()
    for name, spec in specs:
        if spec.get("company") is None and "company" in spec or not direction:
            continue
        key = tuple(sorted((k, v) for k, v in spec.items() if v))
        if key in seen_specs:
            continue
        seen_specs.add(key)
        # 合成演示的公司不在大厂名单里，演示时不限大厂
        rows, _ = select_people(s, PeersSearchArgs(since_days=since_days, big_only=not get_settings().demo_mode, limit=30,
                                                   **{k: v for k, v in spec.items() if v}), now)
        people = [p for p, _ in rows]
        offers = [p for p in people if p.outcome == "拿到offer"]
        layers.append({"layer": name, "all": _tier_stats(people), "offer": _tier_stats(offers),
                       "examples": [{"ref": f"R-{raw.id}", "stage": p.stage, "school_bucket": school_bucket(p.school),
                                     "outcome": p.outcome, "reached": p.reached,
                                     "published": p.published_at.date().isoformat() if p.published_at else None}
                                    for p, raw in rows[:5]],
                       "enough": len(people) >= 5})
    facts = card(s)
    value = {f.attribute: f.value for f in facts}
    you = {"stage": value.get("degree"), "school": value.get("school"), "school_bucket": my_school_bucket(value.get("school")),
           "papers": value.get("paper_experience"), "internships": value.get("internship_experience")}
    lines = []
    for layer in layers:
        o = layer["offer"]
        if o["people"] == 0:
            lines.append(f"{layer['layer']}：没有找到拿到 offer 的自述，看不出这一层的情况")
            continue
        if o["people"] < 5:
            lines.append(f"{layer['layer']}：拿到 offer 的自述只有 {o['people']} 人，样本太少，只能看个例，不能当门槛")
            continue
        top = o["school"]["写了 985/211 类"]
        stated = o["people"] - o["school"]["没写"]
        lines.append(f"{layer['layer']}：拿到 offer 的 {o['people']} 人里，写了学校的 {stated} 人，其中 985/211 类 {top} 人；"
                     f"写了论文的 {o['papers_stated']} 人；实习段数已知 {o['internships_known']} 人"
                     + (f"，中位数 {o['internships_median']} 段" if o["internships_median"] is not None else ""))
    return {
        "scope": {"company": company, "direction": direction, "job_type": job_type, "since_days": since_days},
        "you": you, "layers": layers, "reading": lines,
        "note": ("同类样本是网上发帖人的自述，拿到 offer 的人更爱发；只能说明“发帖的人里有这样的”，不代表录用标准，也不能算个人录用概率。"
                 "“没写”不等于没有。学校只按帖子原话粗分；你的学校只认 985 名单。"),
    }
