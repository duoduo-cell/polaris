"""每日流程里"主动"的那一段（M6）：从库里找今天的候选 → 按你的卡片打分 → 推 / 简报 / 不提 → 记下每个决定 → 写简报。

- 你的条件来自记忆卡片（memory/update.card），不是写死的配置；卡片里还没有方向和城市时不推，先请你聊聊想找什么。
- 候选：最近几天新出的大厂岗位、近期截止的岗位、新的内推 / 招聘 / 面经帖。
- 已经推过或放进过简报的不再提；只放进过简报、现在快截止的，还会再判断一次（截止提醒）。
- 推送走系统通知（Windows 通知中心）；简报写到 data/briefs/日期.md（会有帖子内容，只在本机）。
"""
import re
import tomllib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from polaris.align.normalize import cities as split_cities
from polaris.config import ROOT
from polaris.db.models import Job, Notification, Post, RawItem
from polaris.db.session import session_scope
from polaris.memory.update import card
from polaris.proactive.decide import Decision, Item, Level, Profile, Sent, decide

SETTINGS = ROOT / "config" / "proactive.toml"
BRIEFS = ROOT / "data" / "briefs"
LOOKBACK_DAYS = 3
BRIEF_MAX = 12  # 简报最多列几条；放不下的不记，明天再排（越旧分越低）
FIRST_BRIEF = 10  # 第一次跑：库里已有的全算"新"的，不推送，只挑最对得上的几条进简报
DEADLINE_DAYS = 7
REMIND_DAYS = 3  # 只进过简报的岗位，截止前这么多天再判断一次


def settings() -> dict[str, Any]:
    if SETTINGS.exists():
        return tomllib.loads(SETTINGS.read_text(encoding="utf-8"))
    return {"level": "normal"}


def profile_from_memory(now: datetime) -> Profile:
    with session_scope() as s:
        facts = card(s, now.date())
        by: dict[str, list[Any]] = {}
        for f in facts:
            by.setdefault(f.attribute, []).append(f)
        values = {k: [f.value for f in v] for k, v in by.items()}

        def num(attr: str) -> int | None:
            m = re.search(r"\d+", (values.get(attr) or [""])[0])
            return int(m.group()) if m else None

        stopped = re.compile(r"挂|没过|未通过|结束|拒|放弃")
        interviewing = [f.qualifier for f in by.get("interview_stage", []) if f.qualifier and not stopped.search(f.value)]
    return Profile(roles=values.get("target_role", []), cities=values.get("target_city", []),
                   job_type=(values.get("job_type") or [None])[0], days_per_week=num("days_per_week"),
                   duration_months=num("duration_months"), companies_like=values.get("company_like", []),
                   companies_avoid=values.get("company_avoid", []), constraints=values.get("constraint", []),
                   skills=values.get("skill", []), interviewing=interviewing, big_only=True)


def candidates(now: datetime) -> list[Item]:
    since = now - timedelta(days=LOOKBACK_DAYS)
    soon = now.date() + timedelta(days=DEADLINE_DAYS)
    items: list[Item] = []
    with session_scope() as s:
        jobs = s.execute(select(Job).where(Job.is_synthetic.is_(False), Job.status == "active", Job.company_id.is_not(None),
                                           (Job.first_seen_at >= since) | ((Job.deadline >= now.date()) & (Job.deadline <= soon))))
        for j in jobs.scalars():
            reqs = " ".join(map(str, j.requirements or []))[:800]
            items.append(Item(key=f"job:{j.key}", kind="job", title=j.title or "", company=j.company, cities=list(j.cities or []),
                              job_type=j.job_type, directions=list(j.directions or []), text=reqs,
                              days_per_week=j.days_per_week, duration_months=j.duration_months, deadline=j.deadline,
                              first_seen=j.first_seen_at, cite=f"J-{j.id}"))
        posts = s.execute(select(Post, RawItem).join(RawItem, RawItem.id == Post.raw_item_id).where(
            Post.post_type.in_(("内推", "招聘", "面经")), Post.published_at >= since))
        for p, r in posts:
            companies = p.big_companies or p.companies or []
            items.append(Item(key=f"post:{r.source}:{r.external_id}", kind="post", title=(p.summary or "")[:60],
                              company=companies[0] if companies else None, cities=sorted(split_cities(p.city)),
                              job_type=p.job_type,
                              directions=list(p.directions or []), text=p.summary or "", post_type=p.post_type,
                              has_referral=p.has_referral, first_seen=p.published_at, simhash=r.simhash, cite=f"P-{p.id}"))
    return items


def history(now: datetime) -> tuple[list[Sent], set[str]]:
    """（推过的，只进过简报的编号）。"""
    with session_scope() as s:
        rows = s.execute(select(Notification).where(Notification.created_at >= now - timedelta(days=30),
                                                    Notification.channel.in_(("push", "brief")))).scalars().all()
        sent = [Sent(key=n.item_key, company=n.company, directions=list(n.directions or []), sent_at=n.created_at,
                     feedback=n.feedback, simhash=n.simhash) for n in rows if n.channel == "push"]
        briefed = {n.item_key for n in rows if n.channel == "brief"}
        pushed = {n.item_key for n in rows if n.channel == "push"}
    return sent, briefed - pushed


def _ever_ran() -> bool:
    with session_scope() as s:
        return s.execute(select(Notification.id).limit(1)).first() is not None


def run_daily(now: datetime | None = None, *, level: Level | None = None, dry_run: bool = False,
              notify_fn=None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    level = level or settings().get("level", "normal")
    p = profile_from_memory(now)
    if not p.roles and not p.cities:
        return {"skipped": "还不了解你想找什么（卡片里没有方向和城市），先和北极星聊聊", "decisions": []}
    sent, briefed = history(now)
    items = []
    for it in candidates(now):
        if it.key in briefed:  # 进过简报的不再提，快截止的除外
            if not (it.deadline and 0 <= (it.deadline - now.date()).days <= REMIND_DAYS):
                continue
        items.append(it)
    first_run = not sent and not briefed and not _ever_ran()
    decisions = decide(items, p, sent, now, "off" if first_run else level)
    by_key = {it.key: it for it in items}
    # 简报放不下的不记（明天再排）；第一次跑只留最对得上的几条
    cap = FIRST_BRIEF if first_run else BRIEF_MAX
    briefs = sorted((d for d in decisions if d.channel == "brief"), key=lambda d: (-d.score, d.key))
    overflow = {d.key for d in briefs[cap:]}
    decisions = [d for d in decisions if d.key not in overflow]
    if not dry_run:
        with session_scope() as s:
            for d in decisions:
                it = by_key[d.key]
                s.add(Notification(item_key=d.key, kind=it.kind, cite=it.cite, title=it.title, company=it.company,
                                   directions=it.directions, channel=d.channel, score=d.score, match=d.match,
                                   novelty=d.novelty, urgency=d.urgency, reasons=d.reasons, simhash=it.simhash,
                                   level=level, created_at=now))
        pushes = [d for d in decisions if d.channel == "push"]
        if pushes:
            from polaris.logins import notify

            for d in pushes:
                (notify_fn or notify)("北极星", f"{by_key[d.key].title[:40]}（{by_key[d.key].cite}）：{'；'.join(d.reasons[:2])}")
        write_brief(now, decisions, by_key)
    counts = {c: sum(d.channel == c for d in decisions) for c in ("push", "brief", "drop")}
    return {"candidates": len(items), **counts, "not_listed": len(overflow), "first_run": first_run, "level": level,
            "decisions": [{"cite": by_key[d.key].cite, "title": by_key[d.key].title, "channel": d.channel,
                           "score": round(d.score, 3), "reasons": d.reasons} for d in decisions]}


def write_brief(now: datetime, decisions: list[Decision], by_key: dict[str, Item]) -> None:
    from collections import Counter

    day = now.date().isoformat()
    lines = [f"# 北极星简报 {day}", ""]
    for channel, title in (("push", "已经推给你的"), ("brief", "今天值得看的")):
        ds = sorted((d for d in decisions if d.channel == channel), key=lambda d: (-d.score, d.key))
        if ds:
            lines += [f"## {title}", ""]
            for d in ds:
                it = by_key[d.key]
                lines.append(f"- **{it.title}**（{it.company or '—'}，{it.cite}）：{'；'.join(d.reasons)}")
            lines.append("")
    dropped = [d for d in decisions if d.channel == "drop"]
    if dropped:
        why = Counter(d.reasons[-1] if d.reasons else "—" for d in dropped)
        lines += [f"另外 {len(dropped)} 条没提：" + "，".join(f"{k} {v} 条" for k, v in why.most_common(6)), ""]
    BRIEFS.mkdir(parents=True, exist_ok=True)
    path = BRIEFS / f"{day}.md"
    previous = path.read_text(encoding="utf-8") + "\n" if path.exists() else ""
    path.write_text(previous + "\n".join(lines), encoding="utf-8")
