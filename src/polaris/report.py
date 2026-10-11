"""采集日报：今天各来源新采了多少、涉及哪些大厂、有没有被拦。也是以后"每天早上汇报"的雏形。"""
from collections import Counter
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from polaris.db.models import Post, Posting, RawItem, Run, SearchQuery
from polaris.db.session import session_scope

BEIJING = timezone(timedelta(hours=8))


def day_range(day: datetime | None = None) -> tuple[datetime, datetime]:
    d = (day or datetime.now(BEIJING)).astimezone(BEIJING)
    start = d.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def daily(day: datetime | None = None) -> dict[str, Any]:
    start, end = day_range(day)
    with session_scope() as s:
        new = dict(s.execute(select(RawItem.source, func.count()).where(
            RawItem.first_seen_at >= start, RawItem.first_seen_at < end, RawItem.is_synthetic.is_(False))
            .group_by(RawItem.source)).all())
        total = dict(s.execute(select(RawItem.source, func.count()).where(RawItem.is_synthetic.is_(False))
                               .group_by(RawItem.source)).all())
        runs = s.execute(select(Run.params, Run.stats, Run.status).where(
            Run.kind == "collect", Run.started_at >= start, Run.started_at < end)).all()
        posts = s.execute(select(Post.post_type, Post.big_companies, Post.has_referral).join(
            RawItem, RawItem.id == Post.raw_item_id).where(RawItem.first_seen_at >= start, RawItem.first_seen_at < end)).all()
        jobs = s.execute(select(Posting.big_company).join(RawItem, RawItem.id == Posting.raw_item_id).where(
            RawItem.first_seen_at >= start, RawItem.first_seen_at < end, Posting.big_company.is_not(None))).scalars().all()
        discovered = s.execute(select(SearchQuery.query).where(
            SearchQuery.origin == "discovered", SearchQuery.created_at >= start, SearchQuery.created_at < end)).scalars().all()
    calls = Counter()
    failures = Counter()
    stops: list[str] = []
    for params, stats, _status in runs:
        src = (params or {}).get("source", "?")
        calls[src] += (stats or {}).get("calls", 0) or 0
        failures[src] += (stats or {}).get("failures", 0) or 0
        if (stats or {}).get("stopped"):
            stops.append(f"{src}：{stats['stopped']}")
    companies = Counter(c for _, big, _ in posts for c in (big or [])) + Counter(jobs)
    return {"date": start.astimezone(BEIJING).date().isoformat(), "new": new, "total": total, "calls": dict(calls),
            "failures": dict(failures), "stops": stops, "post_types": dict(Counter(t for t, _, _ in posts)),
            "referrals": sum(1 for _, _, r in posts if r), "companies": companies.most_common(12),
            "discovered_queries": discovered}


def render(d: dict[str, Any]) -> str:
    lines = [f"# 采集日报 {d['date']}", ""]
    if not d["new"]:
        lines.append("今天还没有新采到的内容。")
    for src, n in sorted(d["new"].items(), key=lambda x: -x[1]):
        extra = f"，调用 {d['calls'][src]} 次" if d["calls"].get(src) else ""
        extra += f"，失败 {d['failures'][src]} 次" if d["failures"].get(src) else ""
        lines.append(f"- {src}：新增 {n} 条（库里共 {d['total'].get(src, 0)} 条）{extra}")
    if d["post_types"]:
        lines.append("- 笔记类型：" + "、".join(f"{k} {v}" for k, v in sorted(d["post_types"].items(), key=lambda x: -x[1]))
                     + f"；能内推的 {d['referrals']} 条")
    if d["companies"]:
        lines.append("- 涉及最多的大厂：" + "、".join(f"{c} {n}" for c, n in d["companies"]))
    if d["discovered_queries"]:
        lines.append("- 新发现的查询词：" + "、".join(d["discovered_queries"]))
    lines.append("- 被拦或提前停下：" + ("；".join(d["stops"]) if d["stops"] else "没有"))
    return "\n".join(lines)
