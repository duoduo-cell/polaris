"""北极星在做的事：正在做、需要你、已安排、今天做完的。给网页"今天"页和侧栏用。

只汇总库里已经有的事实（任务、记忆、岗位跟进、采集记录、推送决定），不推测、不编计划；
比如"跟进"只列你自己在岗位详情里定的日期，北极星不会替你去联系谁。
"""
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from polaris.db.models import ChatTask, ChatThread, Job, JobFollowup, MemoryFact, Notification, RawItem
from polaris.db.session import session_scope
from polaris.memory.schema import ATTRIBUTES
from polaris.report import BEIJING
from polaris.sources import label

LEVELS = {"off": "不推送，只进简报", "low": "每天最多推 1 条", "normal": "每天最多推 3 条", "high": "每天最多推 6 条"}


def _day_start(now: datetime) -> datetime:
    local = now.astimezone(BEIJING)
    return datetime.combine(local.date(), datetime.min.time(), tzinfo=BEIJING)


def _hm(t: datetime) -> str:
    return t.astimezone(BEIJING).strftime("%H:%M")


def agenda(now: datetime | None = None, *, demo: bool = False) -> dict[str, Any]:
    """demo=True：合成演示只汇总演示库里的事（对话、记忆、跟进），不读本机采集、简报和推送设置这些个人文件。"""
    from polaris.match import profile
    from polaris.proactive.daily import BRIEFS, settings

    now = now or datetime.now(UTC)
    today = now.astimezone(BEIJING).date()
    start = _day_start(now)
    doing: list[dict[str, Any]] = []
    needs: list[dict[str, Any]] = []
    scheduled: list[dict[str, Any]] = []
    done: list[dict[str, Any]] = []
    p = profile(now)
    with session_scope() as s:
        for task in s.scalars(select(ChatTask).where(ChatTask.state.in_(("queued", "running")))
                              .order_by(ChatTask.created_at).limit(5)):
            question = (task.payload or {}).get("message") or "你的决定"
            doing.append({"kind": "chat", "text": f"{'在回答' if task.state == 'running' else '排队中'}：{question[:28]}",
                          "href": "/chat", "thread_id": task.thread_id})
        last = None if demo else s.scalar(select(func.max(RawItem.fetched_at)))
        if demo:
            doing.append({"kind": "watch", "text": "合成演示：不运行采集，岗位和面经都是预置的虚构资料"})
        elif p and (p.roles or p.cities):
            what = " · ".join(x for x in ("、".join(p.cities[:2]), "、".join(p.roles[:2])) if x)
            level = settings().get("level", "normal")
            doing.append({"kind": "watch", "text": f"盯着{what}的新岗位和帖子",
                          "detail": f"{LEVELS.get(level, level)}，夜里 23 点到早上 8 点不打扰"
                                    + (f"；上次采集 {_hm(last)}" if last and last >= start else "")})
        else:
            needs.append({"kind": "profile", "text": "先告诉北极星你想找什么：方向、城市、每周几天", "href": "/chat"})

        for thread in s.scalars(select(ChatThread).where(func.jsonb_array_length(ChatThread.approvals) > 0).limit(5)):
            for a in thread.approvals or []:
                needs.append({"kind": "approval", "text": a.get("description") or "有个动作等你批准", "href": "/chat",
                              "thread_id": thread.id})
        for f in s.scalars(select(MemoryFact).where(MemoryFact.status == "pending").order_by(MemoryFact.recorded_at.desc())
                           .limit(5)):
            needs.append({"kind": "confirm", "text": f"要你确认：{f.statement}", "href": "/memory"})

        rows = s.execute(select(JobFollowup, Job).join(Job, Job.id == JobFollowup.job_id)
                         .where(JobFollowup.next_followup.is_not(None)).order_by(JobFollowup.next_followup).limit(20)).all()
        for f, j in rows:
            name = " · ".join(x for x in (j.company or j.company_name, j.title) if x)
            when = f.next_followup
            if when <= today:
                needs.append({"kind": "followup", "when": when.isoformat(), "job": f"J-{j.id}",
                              "text": f"该跟进了：{name}（你定的 {when.month} 月 {when.day} 日）"})
            else:
                scheduled.append({"kind": "followup", "when": when.isoformat(), "job": f"J-{j.id}",
                                  "text": f"跟进 {name}（你定的日期）"})
        from polaris.db.models import Watch, WatchEvent
        from polaris.followups import STATUSES, stale

        urgent = s.execute(select(WatchEvent, Watch).join(Watch, Watch.id == WatchEvent.watch_id).where(
            Watch.status == "active", WatchEvent.importance == "urgent", WatchEvent.read_at.is_(None),
            WatchEvent.watch_version == Watch.version).order_by(WatchEvent.id.desc()).limit(5)).all()
        for e, w in urgent:
            needs.append({"kind": "watch", "text": f"跟进「{w.title}」：{e.title}", "href": f"/watches?id={w.id}"})
        active_watches = s.execute(select(Watch).where(Watch.status == "active")).scalars().all()
        for w in active_watches:
            if w.next_check_at:
                scheduled.append({"kind": "watch", "when": w.next_check_at.astimezone(BEIJING).strftime("%H:%M"),
                                  "text": f"检查跟进卡「{w.title}」", "href": f"/watches?id={w.id}"})

        for f, j, days in stale(s, now)[:5]:
            name = " · ".join(x for x in (j.company or j.company_name, j.title) if x)
            needs.append({"kind": "followup", "job": f"J-{j.id}",
                          "text": f"{STATUSES[f.status]} {days} 天没有新进展：{name}，可以起草一条跟进消息"})
        for f in s.scalars(select(MemoryFact).where(MemoryFact.status == "active", MemoryFact.valid_from > today)
                           .order_by(MemoryFact.valid_from).limit(10)):
            scheduled.append({"kind": "memory", "when": f.valid_from.isoformat(), "detail": "到时自动生效",
                              "text": f"{ATTRIBUTES[f.attribute].label}：{f.value}" if f.attribute in ATTRIBUTES else f.statement})
        brief_today = (BRIEFS / f"{today.isoformat()}.md").exists()
        if demo:
            pass
        elif not brief_today:
            scheduled.insert(0, {"kind": "brief", "when": "08:30", "text": "出今日简报（有你的条件时）"})
        else:
            done.append({"kind": "brief", "when": "今天", "text": "今日简报已经出了", "href": "/"})

        fetched = [] if demo else s.execute(select(RawItem.source, func.count()).where(RawItem.fetched_at >= start)
                                            .group_by(RawItem.source)).all()
        if fetched:
            total = sum(n for _, n in fetched)
            parts = "、".join(f"{label(src)} {n}" for src, n in sorted(fetched, key=lambda x: -x[1]))
            done.append({"kind": "collect", "when": _hm(last) if last else "今天", "text": f"采到 {total} 条新内容（{parts}）"})
        new_jobs = 0 if demo else s.scalar(select(func.count()).select_from(Job).where(
            Job.first_seen_at >= start, Job.is_synthetic.is_(False))) or 0
        if new_jobs:
            done.append({"kind": "jobs", "when": "今天", "text": f"发现 {new_jobs} 个新岗位", "href": "/jobs"})
        sent = dict(s.execute(select(Notification.channel, func.count()).where(Notification.created_at >= start)
                              .group_by(Notification.channel)).all())
        if sent:
            done.append({"kind": "proactive", "when": "今天", "href": "/",
                         "text": f"推给你 {sent.get('push', 0)} 条，进简报 {sent.get('brief', 0)} 条，看过没提 {sent.get('drop', 0)} 条"})
    return {"now": now.isoformat(), "doing": doing, "needs_you": needs, "scheduled": scheduled[:8], "done": done,
            "last_collect": last.isoformat() if last else None}
