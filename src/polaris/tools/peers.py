"""peers_search：拿到或没拿到某个岗位的人是什么背景（10-12）。

数据来自小红书、脉脉帖子里自述的背景和结果（`polaris/peers.py` 抽取，带原句）。用户问"什么背景能拿到""我的竞争力""这个岗难不难"时用。
每条都带发帖日期、距今几天和原帖编号（R-），回答时要说样本有多少、是什么时候的，样本少就别下结论。
"""
import re
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from polaris.companies import match
from polaris.db.models import PeerProfile, RawItem
from polaris.db.session import session_scope
from polaris.sources import label
from polaris.tools.base import ToolSpec, untrusted


class PeersSearchArgs(BaseModel):
    company: str | None = Field(None, description="目标公司，大厂简称、产品名都行，如 字节、豆包、腾讯")
    direction: str | None = Field(None, description="方向关键词，如 Agent、后训练、RAG、算法；在方向、岗位、团队里找")
    stage: Literal["本科", "硕士", "博士", "已工作"] | None = Field(None, description="只看某个阶段的人")
    outcome: Literal["拿到offer", "挂了", "进行中"] | None = Field(None, description="只看某种结果")
    job_type: Literal["实习", "日常实习", "暑期实习", "校招", "社招"] | None = Field(
        None, description="实习包含日常和暑期，不包含校招和社招；未写类型的不会冒充实习")
    big_only: bool = Field(True, description="默认只看明确的大厂目标；未知公司不用于推断大厂门槛")
    since_days: int = Field(180, ge=1, le=730, description="只看最近多少天发的帖子，默认半年")
    limit: int = Field(10, ge=1, le=30)


NO_INTERN = re.compile(r"[0零无]\s*段?\s*实习(?!岗|薪)|没有?实习(?:过|经历|经验)?(?=[，,。；;\s]|$)")
NOT_STATED = "没写（帖子里没提，不等于没有）"


def _internships(p: PeerProfile, raw: RawItem) -> list[str] | str:
    """10-12 实测：没写实习时返回空列表，模型说成了"0 实习"（R-997 原文只写了学历，评论里还有人问"上一段实习是后端嘛"）。"""
    if p.internships:
        return [" ".join(x for x in (i.get("company"), i.get("team"), f"{i['months']}个月" if i.get("months") else None) if x)
                for i in p.internships]
    return "原句写了没有实习" if NO_INTERN.search(p.evidence or "") else NOT_STATED  # 只看这个人的原句，正文里有别人的话


def _row(p: PeerProfile, raw: RawItem, now: datetime) -> dict[str, Any]:
    days = (now - p.published_at).days if p.published_at else None
    return {
        "id": f"R-{raw.id}", "source": label(raw.source), "url": raw.url,
        "published": p.published_at.date().isoformat() if p.published_at else None, "days_ago": days,
        "who": p.who, "stage": p.stage, "grad_year": p.grad_year, "school": p.school, "major": p.major,
        "papers": p.paper_summary or (f"{len(p.papers)} 篇" if p.papers else NOT_STATED), "top_papers": p.top_papers,
        "internships": _internships(p, raw),
        "awards": p.awards, "target": " / ".join(x for x in (p.target_company, p.target_team, p.target_role) if x) or None,
        "job_type": p.job_type, "direction": p.direction, "outcome": p.outcome, "reached": p.reached,
        "difficulty": p.difficulty, "dates": p.dates, "from_image": p.from_image,
        "evidence": untrusted(p.evidence[:400]),
    }


def _dedupe(rows: list[Any]) -> list[Any]:
    """同一个人发了好几条帖子（求内推的人常常一天发两三条），只留最新的一条：
    小红书按发帖账号 + 阶段 + 目标公司；脉脉没有账号，按阶段、学校、目标公司、结果和原句开头。"""
    seen, out = set(), []
    for p, raw in rows:
        author = (((raw.raw_json or {}).get("note") or {}).get("user") or {}).get("userId")
        key = ((author, p.stage, p.target_company) if author and p.who == "作者"
               else (p.stage, p.school, p.target_company, p.outcome, re.sub(r"\W", "", p.evidence or "")[:30]))
        if key not in seen:
            seen.add(key)
            out.append((p, raw))
    return out


def select_people(s: Any, args: PeersSearchArgs, now: datetime) -> tuple[list[Any], int]:
    """按条件找人（去重后，按发帖时间从新到旧），以及因公司未知被排除的记录数。peers_search 和 peers_compare 共用。"""
    stmt = (select(PeerProfile, RawItem).join(RawItem, RawItem.id == PeerProfile.raw_item_id)
            .where(PeerProfile.published_at >= now - timedelta(days=args.since_days), PeerProfile.published_at <= now))
    if args.company:
        big = match(args.company)
        name = big.name if big else args.company
        stmt = stmt.where(or_(PeerProfile.big_company == name, PeerProfile.target_company.ilike(f"%{args.company}%"),
                              PeerProfile.target_team.ilike(f"%{args.company}%")))
    if args.direction:
        d = f"%{args.direction}%"
        stmt = stmt.where(or_(PeerProfile.direction.ilike(d), PeerProfile.target_role.ilike(d), PeerProfile.target_team.ilike(d)))
    for col, value in ((PeerProfile.stage, args.stage), (PeerProfile.outcome, args.outcome)):
        if value:
            stmt = stmt.where(col == value)
    if args.job_type == "实习":
        stmt = stmt.where(PeerProfile.job_type.in_(("日常实习", "暑期实习")))
    elif args.job_type:
        stmt = stmt.where(PeerProfile.job_type == args.job_type)
    excluded_unknown_company = 0
    if args.big_only:
        unknown = stmt.where(PeerProfile.big_company.is_(None)).subquery()
        excluded_unknown_company = s.scalar(select(func.count()).select_from(unknown)) or 0
        stmt = stmt.where(PeerProfile.big_company.is_not(None))
    rows = _dedupe(s.execute(stmt.order_by(PeerProfile.published_at.desc())).all())
    return rows, excluded_unknown_company


def peers_search(args: PeersSearchArgs) -> dict[str, Any]:
    now = datetime.now(UTC)
    with session_scope() as s:
        rows, excluded_unknown_company = select_people(s, args, now)
        people = [p for p, _ in rows]
        items = [_row(p, raw, now) for p, raw in rows[: args.limit]]
    known_stage = [p for p in people if p.stage in ("本科", "硕士", "博士", "已工作")]
    explicit_none = [p for p in people if not p.internships and NO_INTERN.search(p.evidence or "")]
    internship_counts = [len(p.internships) for p in people if p.internships] + [0] * len(explicit_none)
    summary = {
        "matched": len(people),
        "by_outcome": {k: sum(1 for p in people if p.outcome == k) for k in ("拿到offer", "挂了", "进行中", "放弃", "未说明")},
        "by_stage": {k: sum(1 for p in known_stage if p.stage == k) for k in ("本科", "硕士", "博士", "已工作")},
        "stage_not_stated": len(people) - len(known_stage),
        "stated_papers": sum(1 for p in people if p.papers or p.paper_summary),
        "papers_not_stated": sum(1 for p in people if not p.papers and not p.paper_summary),
        "with_top_paper": sum(1 for p in people if p.top_papers),
        "stated_internships": sum(1 for p in people if p.internships),
        "explicit_no_internships": len(explicit_none),
        "internships_not_stated": sum(1 for p in people if not p.internships) - len(explicit_none),
        "internships_median": median(internship_counts) if internship_counts else None,
        "newest": people[0].published_at.date().isoformat() if people and people[0].published_at else None,
        "oldest": people[-1].published_at.date().isoformat() if people and people[-1].published_at else None,
    }
    out: dict[str, Any] = {"summary": summary, "returned": len(items), "items": items,
                           "scope": {"company": args.company, "direction": args.direction, "stage": args.stage,
                                     "outcome": args.outcome, "job_type": args.job_type, "big_only": args.big_only,
                                     "since_days": args.since_days, "from_date": (now - timedelta(days=args.since_days)).date().isoformat(),
                                     "to_date": now.date().isoformat(),
                                     "excluded_unknown_company_records": excluded_unknown_company},
                           "note": "来自帖子里本人自述的背景和结果，只是在网上发帖的那部分人，不代表全部候选人；回答时说清样本量和日期。"
                                   "“没写”是帖子里没提，不能说成 0 段实习或没有论文。不同查询的样本可能重叠，不能把数量直接相加。"
                                   "未知学历必须单列；校招、产品岗和技术实习不能混成同一门槛。本人感受不代表招聘标准"}
    if not people:
        out["hint"] = "库里还没有符合条件的人。可以放宽条件（去掉阶段或结果、加大天数），或者用 platform_search 去小红书现搜"
    elif len(people) < 5:
        out["hint"] = f"只有 {len(people)} 个人，样本太少，不要据此下“普遍”的结论"
    return out


TOOLS = [
    ToolSpec(name="peers_search",
             description="查拿到或没拿到某个岗位的人是什么背景：阶段、学校、论文、实习段数、结果、面到第几轮、对难度的原话、日期。"
                         "来自小红书和脉脉帖子里本人自述，带原句和原帖编号（R-）。用户问\"什么背景能拿到\"\"我的竞争力\"\"这个岗难不难\""
                         "\"同阶段的人什么水平\"时用；返回里有汇总和样本量。",
             args_schema=PeersSearchArgs, func=peers_search, example='{"company": "字节", "direction": "Agent", "outcome": "拿到offer"}'),
]
