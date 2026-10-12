"""peers_search：拿到或没拿到某个岗位的人是什么背景（10-12）。

数据来自小红书、脉脉帖子里自述的背景和结果（`polaris/peers.py` 抽取，带原句）。用户问"什么背景能拿到""我的竞争力""这个岗难不难"时用。
每条都带发帖日期、距今几天和原帖编号（R-），回答时要说样本有多少、是什么时候的，样本少就别下结论。
"""
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import or_, select

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
    job_type: Literal["日常实习", "暑期实习", "校招", "社招"] | None = None
    since_days: int = Field(180, ge=1, le=730, description="只看最近多少天发的帖子，默认半年")
    limit: int = Field(10, ge=1, le=30)


def _row(p: PeerProfile, raw: RawItem, now: datetime) -> dict[str, Any]:
    days = (now - p.published_at).days if p.published_at else None
    return {
        "id": f"R-{raw.id}", "source": label(raw.source), "url": raw.url,
        "published": p.published_at.date().isoformat() if p.published_at else None, "days_ago": days,
        "who": p.who, "stage": p.stage, "grad_year": p.grad_year, "school": p.school, "major": p.major,
        "papers": p.paper_summary or (f"{len(p.papers)} 篇" if p.papers else None), "top_papers": p.top_papers,
        "internships": [" ".join(x for x in (i.get("company"), i.get("team"), f"{i['months']}个月" if i.get("months") else None) if x)
                        for i in p.internships],
        "awards": p.awards, "target": " / ".join(x for x in (p.target_company, p.target_team, p.target_role) if x) or None,
        "job_type": p.job_type, "direction": p.direction, "outcome": p.outcome, "reached": p.reached,
        "difficulty": p.difficulty, "dates": p.dates, "from_image": p.from_image,
        "evidence": untrusted(p.evidence[:400]),
    }


def peers_search(args: PeersSearchArgs) -> dict[str, Any]:
    now = datetime.now(UTC)
    with session_scope() as s:
        stmt = (select(PeerProfile, RawItem).join(RawItem, RawItem.id == PeerProfile.raw_item_id)
                .where(PeerProfile.published_at >= now - timedelta(days=args.since_days)))
        if args.company:
            big = match(args.company)
            name = big.name if big else args.company
            stmt = stmt.where(or_(PeerProfile.big_company == name, PeerProfile.target_company.ilike(f"%{args.company}%"),
                                  PeerProfile.target_team.ilike(f"%{args.company}%")))
        if args.direction:
            d = f"%{args.direction}%"
            stmt = stmt.where(or_(PeerProfile.direction.ilike(d), PeerProfile.target_role.ilike(d), PeerProfile.target_team.ilike(d)))
        for col, value in ((PeerProfile.stage, args.stage), (PeerProfile.outcome, args.outcome), (PeerProfile.job_type, args.job_type)):
            if value:
                stmt = stmt.where(col == value)
        rows = s.execute(stmt.order_by(PeerProfile.published_at.desc())).all()
        people = [p for p, _ in rows]
        items = [_row(p, raw, now) for p, raw in rows[: args.limit]]
    known_stage = [p for p in people if p.stage != "未说明"]
    summary = {
        "matched": len(people),
        "by_outcome": {k: sum(1 for p in people if p.outcome == k) for k in ("拿到offer", "挂了", "进行中", "放弃", "未说明")},
        "by_stage": {k: sum(1 for p in known_stage if p.stage == k) for k in ("本科", "硕士", "博士", "已工作")},
        "stated_papers": sum(1 for p in people if p.papers or p.paper_summary),
        "with_top_paper": sum(1 for p in people if p.top_papers),
        "stated_internships": sum(1 for p in people if p.internships),
        "internships_median": median([len(p.internships) for p in people if p.internships]) if any(p.internships for p in people) else None,
        "newest": people[0].published_at.date().isoformat() if people and people[0].published_at else None,
        "oldest": people[-1].published_at.date().isoformat() if people and people[-1].published_at else None,
    }
    out: dict[str, Any] = {"summary": summary, "returned": len(items), "items": items,
                           "note": "来自帖子里本人自述的背景和结果，只是在网上发帖的那部分人，不代表全部候选人；回答时说清样本量和日期"}
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
