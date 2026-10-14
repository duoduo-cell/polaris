"""主动决策（M6，北极星自己实现，`TECH.md` 第 3 节环节 6）：一条新消息（新岗位、内推帖、截止日临近、面经）值不值得打扰你。

三个分数，全部由规则算，每一步都写理由（对标 Muse "判断新结果值不值得通知、频率可调"，做成可核实的）：
- 匹配度：和你的卡片（记忆）对不对得上。你说过不想去的公司、不接受的条件（外包、单休）、不对的岗位类型、
  不在你说的城市、和你的方向完全不沾边的，直接不提；到岗天数或实习时长不够要扣分（留给你自己判断）；
  想去的公司、会的技术对得上要加分。你连着关掉同一家或同一方向的推送，也会扣分。
- 新颖度：推过的不再推；和推过的帖子几乎一样（搬运帖）算推过；越新的越高。
- 紧迫度：截止日越近越高；你正在面的公司出了新面经、内推帖也算紧迫。
综合分 = 0.5 × 匹配度 + 0.3 × 新颖度 + 0.2 × 紧迫度。

去向三种：push 现在推给你；brief 放进每日简报；drop 不提（写明为什么）。
克制：匹配度不够的不推；到岗天数、实习时长和你说的对不上的，只进简报让你自己判断；面经是备考材料，只有你正在面这家时才推；推送有每天的上限（按你设的主动程度）；
夜里不推，挪到简报；主动程度设成"关"就只进简报。
10-11 在开发集场景上第一次跑：17 条里错 4 条（杭州的岗位、算法岗进了简报；没在面的公司的面经、上海的招聘帖被推了），
据此加了上面"城市、方向不沾边直接不提""面经只在正在面时推""条件对不上只进简报"三条。
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from polaris.align.normalize import split_title
from polaris.companies import match as big_company
from polaris.dedup import hamming
from polaris.report import BEIJING

Level = Literal["off", "low", "normal", "high"]
PUSH_THRESHOLD = {"low": 0.75, "normal": 0.65, "high": 0.55}
DAILY_PUSHES = {"off": 0, "low": 1, "normal": 3, "high": 6}
BRIEF_THRESHOLD = 0.4
MIN_MATCH_TO_PUSH = 0.6
QUIET = (time(23, 0), time(8, 0))
DUP_SIMHASH = 10  # 帖子近似重复（dedup.py 在笔记上标定的阈值）
FATIGUE_DISMISSALS = 2  # 两周内同一家公司或方向被你关掉几次就开始扣分
JOB_TYPE_WORDS = {"实习": "intern", "日常实习": "intern", "暑期实习": "intern", "校招": "campus", "社招": "social"}
JOB_TYPE_NAMES = {"intern": "实习", "campus": "校招", "social": "社招"}
# 方向 = 概念（Agent、大模型、RAG……）+ 职能（开发、算法、产品……）。只看概念会把"Agent 开发"和"Agent 产品""Agent 算法"混为一谈
# （10-11 在真实数据上试跑时，德勤的数据科学内推、百度的 AI 产品实习都被当成"Agent 开发"推了），所以职能也要对得上。
FUNCTIONS = {
    "开发": ("开发", "研发", "工程", "后端", "前端", "全栈", "架构", "engineering", "engineer", "developer", "infra"),
    "算法": ("算法", "研究", "researcher", "scientist", "algorithm"),
    "产品": ("产品", "product"),
    "运营": ("运营",), "测试": ("测试",), "设计": ("设计",), "数据": ("数据分析", "数据科学", "data scientist"),
    "销售": ("销售", "市场", "商务"),
}
REVIEW = "要你自己判断："  # 理由带这个前缀的，最多进简报，不主动推


@dataclass
class Item:
    key: str  # 稳定编号：岗位用 jobs.key，帖子用来源编号
    kind: Literal["job", "post"]
    title: str
    company: str | None = None
    cities: list[str] = field(default_factory=list)
    job_type: str | None = None  # intern / campus / social
    directions: list[str] = field(default_factory=list)
    text: str = ""  # 岗位名 + 要求，或帖子概括，用来找关键词
    days_per_week: int | None = None
    duration_months: int | None = None
    deadline: date | None = None
    first_seen: datetime | None = None
    post_type: str | None = None  # 内推 / 招聘 / 面经 / ……
    has_referral: bool = False
    simhash: int | None = None
    cite: str = ""  # 推给你时写的编号，如 J-12、P-3


@dataclass
class Sent:
    key: str
    company: str | None
    directions: list[str]
    sent_at: datetime
    feedback: Literal["useful", "dismissed", None] = None
    simhash: int | None = None


@dataclass
class Profile:
    """从卡片里取出的、和推送有关的部分（proactive/profile.py 从记忆生成）。"""
    roles: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    job_type: str | None = None
    days_per_week: int | None = None
    duration_months: int | None = None
    companies_like: list[str] = field(default_factory=list)
    companies_avoid: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)  # 不接受外包、单休……
    skills: list[str] = field(default_factory=list)
    interviewing: list[str] = field(default_factory=list)  # 正在面的公司
    big_only: bool = False  # 只看风向标大厂（用户 10-11 的决定，日常流程里打开）


@dataclass
class Decision:
    key: str
    channel: Literal["push", "brief", "drop"]
    score: float
    match: float
    novelty: float
    urgency: float
    reasons: list[str]


def _company(name: str | None) -> str | None:
    if not name:
        return None
    big = big_company(name)
    return big.name if big else name


def _low(s: str) -> str:
    return s.lower().replace(" ", "")


def _functions(text: str) -> set[str]:
    t = _low(text)
    return {f for f, words in FUNCTIONS.items() if any(w in t for w in words)}


def role_hit(role: str, item: "Item") -> float:
    """用户的方向（如"Agent 开发"）和这条对得上多少：0 对不上；0.6 概念只出现在正文或方向标签里；1 概念在标题里。
    用户的方向带职能时，这条的标题（帖子用概括）里出现的职能要有一个对得上，标题里没写职能就不卡。
    （10-11 真实数据上试跑：方向标签是抽取时打的，偏宽，"大数据开发"也带着 Agent 标签，所以标题里有才算强匹配。）"""
    want_funcs = _functions(role)
    concept = _low(role)
    # 长词先剥离，避免先删除engineer后把Engineering残留为ing，造成同义职能不匹配。
    for word in sorted({w for words in FUNCTIONS.values() for w in words}, key=len, reverse=True):
        concept = concept.replace(word, "")
    concept = concept.strip("-·/、 ") or _low(role)
    text = _low(f"{item.title} {item.text}")
    if not (concept in text or any(concept in _low(d) or _low(d) == concept for d in item.directions)):
        return 0.0
    if want_funcs:
        # 岗位只看标题里的岗位名部分：部门名会混进职能词（"GUI Agent算法实习生 - 开发者服务"里的"开发"是部门名）
        have = _functions(split_title(item.title, item.company).role if item.kind == "job" else f"{item.title} {item.text}")
        if have and not have & want_funcs:
            return 0.0
    return 1.0 if concept in _low(item.title) else 0.6


def match_score(item: Item, p: Profile, history: list[Sent], now: datetime) -> tuple[float | None, list[str]]:
    """返回（匹配度，理由）。匹配度为 None 表示硬性排除。"""
    reasons: list[str] = []
    company = _company(item.company)
    text = _low(f"{item.title} {item.text}")
    if company and company in {_company(c) for c in p.companies_avoid}:
        return None, [f"你说过不想去{company}"]
    for c in p.constraints:
        word = c.replace("不接受", "").replace("不想去", "").replace("不要", "").strip()
        if word and _low(word) in text:
            return None, [f"有你不接受的条件：{word}"]
    if p.big_only and company and big_company(company) is None:
        return None, [f"{company}不在你关注的大厂名单里"]
    if p.job_type and item.job_type:
        want = JOB_TYPE_WORDS.get(p.job_type, p.job_type)
        if want != item.job_type:
            return None, [f"类型不对：你找{p.job_type}，这个是{JOB_TYPE_NAMES.get(item.job_type, item.job_type)}"]
    if p.cities and item.cities and not set(c.removesuffix("市") for c in p.cities) & set(item.cities):
        return None, [f"不在你说的城市（{'、'.join(item.cities)}）"]
    score, weight = 0.0, 0.0

    def part(w: float, value: float, why: str | None) -> None:
        nonlocal score, weight
        score, weight = score + w * value, weight + w
        if why:
            reasons.append(why)

    if p.roles:
        strength = {r: role_hit(r, item) for r in p.roles}
        hit = [r for r, v in strength.items() if v > 0]
        if not hit:
            return None, ["和你的方向不沾边"]
        best = max(strength.values())
        part(0.4, best, f"方向对得上：{'、'.join(hit)}" + ("" if best == 1.0 else "（只在要求或标签里提到）"))
    if p.cities and item.cities:
        part(0.25, 1.0, None)
    if p.companies_like:
        liked = company in {_company(c) for c in p.companies_like}
        part(0.15, 1.0 if liked else 0.3, f"是你想去的{company}" if liked else None)
    if p.skills and item.kind == "job":
        hit = [s for s in p.skills if _low(s) in text]
        part(0.1, min(1.0, len(hit) / 2), f"要求里有你会的：{'、'.join(hit)}" if hit else None)
    if item.kind == "job" and p.days_per_week and item.days_per_week and item.days_per_week > p.days_per_week:
        part(0.1, 0.0, f"{REVIEW}要求每周 {item.days_per_week} 天，你说能去 {p.days_per_week} 天")
    if item.kind == "job" and p.duration_months and item.duration_months and item.duration_months > p.duration_months:
        part(0.1, 0.0, f"{REVIEW}要求实习 {item.duration_months} 个月，你说能实习 {p.duration_months} 个月")
    if item.kind == "post" and company and company in {_company(c) for c in p.interviewing}:
        part(0.3, 1.0, f"是你正在面的{company}")
    m = score / weight if weight else 0.5
    since = now - timedelta(days=14)
    tired = [s for s in history if s.feedback == "dismissed" and s.sent_at >= since
             and ((company and _company(s.company) == company) or set(s.directions) & set(item.directions))]
    if len(tired) >= FATIGUE_DISMISSALS:
        m *= 0.7
        reasons.append(f"最近两周你关掉了 {len(tired)} 条同公司或同方向的推送")
    return m, reasons


def novelty_score(item: Item, history: list[Sent], now: datetime) -> tuple[float, str | None]:
    if any(s.key == item.key for s in history):
        return 0.0, "推过了"
    if item.simhash is not None and any(s.simhash is not None and hamming(s.simhash, item.simhash) <= DUP_SIMHASH
                                        for s in history):
        return 0.0, "和推过的帖子几乎一样（搬运）"
    if item.first_seen is None:
        return 0.7, None
    days = max(0.0, (now - item.first_seen).total_seconds() / 86400)
    return max(0.2, 1.0 - days / 7), None


def urgency_score(item: Item, p: Profile, now: datetime) -> tuple[float, str | None]:
    best, why = 0.0, None
    if item.deadline:
        left = (item.deadline - now.date()).days
        if left < 0:
            return -1.0, "已经截止"
        if left <= 7:
            best, why = 1.0 - left / 7 * 0.6, f"{left} 天后截止"
    company = _company(item.company)
    if item.kind == "post" and company and company in {_company(c) for c in p.interviewing} and 0.8 > best:
        best, why = 0.8, f"你正在面{company}"
    if item.kind == "post" and (item.has_referral or item.post_type == "内推") and 0.5 > best:
        best, why = 0.5, "内推帖，早投早处理"
    return best, why


def decide(items: list[Item], p: Profile, history: list[Sent], now: datetime, level: Level = "normal") -> list[Decision]:
    """一批候选一起决定：先各自打分，再按分数从高到低占每天的推送名额。"""
    now = now.astimezone(BEIJING)  # 静默时段、每日名额和截止日统一按北京时间
    out: list[Decision] = []
    by_key = {it.key: it for it in items}
    interviewing = {_company(c) for c in p.interviewing}
    for it in items:
        m, reasons = match_score(it, p, history, now)
        if m is None:
            out.append(Decision(it.key, "drop", 0.0, 0.0, 0.0, 0.0, reasons))
            continue
        n, n_why = novelty_score(it, history, now)
        u, u_why = urgency_score(it, p, now)
        if u < 0:
            out.append(Decision(it.key, "drop", 0.0, m, n, 0.0, reasons + [u_why or "已经截止"]))
            continue
        reasons += [w for w in (n_why, u_why) if w]
        score = 0.5 * m + 0.3 * n + 0.2 * u
        if n == 0.0:
            out.append(Decision(it.key, "drop", score, m, n, u, reasons))
        elif score >= BRIEF_THRESHOLD:
            out.append(Decision(it.key, "brief", score, m, n, u, reasons))
        else:
            out.append(Decision(it.key, "drop", score, m, n, u, reasons + ["和你的情况关系不大"]))
    # 推送名额：今天已经推过的算进去；分数高的先占
    pushed_today = sum(1 for s in history if s.sent_at.astimezone(BEIJING).date() == now.date())
    budget = max(0, DAILY_PUSHES[level] - pushed_today)
    quiet = now.time() >= QUIET[0] or now.time() < QUIET[1]
    for d in sorted((d for d in out if d.channel == "brief"), key=lambda d: (-d.score, d.key)):
        if level == "off":
            break
        if d.score < PUSH_THRESHOLD[level] or d.match < MIN_MATCH_TO_PUSH:
            continue
        it = by_key[d.key]
        if any(r.startswith(REVIEW) for r in d.reasons):
            d.reasons.append("有一项和你说的条件对不上，放进简报让你自己看")
            continue
        if it.kind == "post" and it.post_type == "面经" and _company(it.company) not in interviewing:
            d.reasons.append("面经是备考材料，你没在面这家，放进简报")
            continue
        if quiet:
            d.reasons.append("夜里不打扰，放进早上的简报")
            continue
        if budget <= 0:
            d.reasons.append("今天的推送名额用完了，放进简报")
            continue
        d.channel = "push"
        budget -= 1
    return out


def explain(d: Decision) -> dict[str, Any]:
    return {"key": d.key, "channel": d.channel, "score": round(d.score, 3), "match": round(d.match, 3),
            "novelty": round(d.novelty, 3), "urgency": round(d.urgency, 3), "reasons": d.reasons}
