"""具体岗位的收藏和进度。只有用户明确操作才写入，保留历史、版本和幂等请求。"""
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from polaris.db.models import Job, JobFollowup, JobFollowupEvent

STATUSES = {"untracked": "未投递", "skipped": "暂不考虑", "applied": "已投递", "interview": "面试中",
            "offer": "已获 Offer", "rejected": "未通过", "withdrawn": "已撤回"}


class FollowupConflict(ValueError):
    pass


def state(row: JobFollowup | None) -> dict[str, Any]:
    return {"saved": row.saved if row else False, "status": row.status if row else "untracked",
            "stage": row.stage if row else None, "note": row.note if row else None,
            "next_followup": row.next_followup.isoformat() if row and row.next_followup else None,
            "revision": row.revision if row else 0}


def get(s: Session, job_id: int, *, history: bool = False) -> dict[str, Any]:
    out = state(s.scalar(select(JobFollowup).where(JobFollowup.job_id == job_id)))
    out["status_label"] = STATUSES[out["status"]]
    if history:
        rows = s.scalars(select(JobFollowupEvent).where(JobFollowupEvent.job_id == job_id)
                         .order_by(JobFollowupEvent.id.desc()).limit(30)).all()
        out["history"] = [{"before": e.before, "after": e.after, "source_text": e.source_text,
                           "created_at": e.created_at.isoformat()} for e in rows]
    return out


def states(s: Session, job_ids: list[int]) -> dict[int, dict[str, Any]]:
    rows = {r.job_id: r for r in s.scalars(select(JobFollowup).where(JobFollowup.job_id.in_(job_ids))).all()}
    out = {i: state(rows.get(i)) for i in job_ids}
    for value in out.values():
        value["status_label"] = STATUSES[value["status"]]
    return out


def update(s: Session, job_id: int, changes: dict[str, Any], request_key: str, source_text: str,
           revision: int | None = None) -> dict[str, Any]:
    s.execute(text("select pg_advisory_xact_lock(739514623)"))
    if not s.get(Job, job_id):
        raise LookupError("岗位不存在")
    encoded_changes = {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in changes.items()}
    old_request = s.scalar(select(JobFollowupEvent).where(JobFollowupEvent.request_key == request_key))
    if old_request:
        if old_request.job_id != job_id or old_request.changes != encoded_changes:
            raise FollowupConflict("同一个请求编号不能用于不同的岗位或操作")
        # 同一个响应丢失后的重试不会覆盖后来已经发生的进度。
        return get(s, job_id, history=True)
    row = s.scalar(select(JobFollowup).where(JobFollowup.job_id == job_id))
    before = state(row)
    if revision is not None and revision != before["revision"]:
        raise FollowupConflict("岗位进度已经更新，请重新打开详情再操作")
    if not changes or not changes.keys() <= {"saved", "status", "stage", "note", "next_followup"}:
        raise ValueError("没有有效的岗位操作")
    if "status" in changes and changes["status"] not in STATUSES:
        raise ValueError("不支持的投递状态")
    if row is None:
        row = JobFollowup(job_id=job_id)
        s.add(row)
        s.flush()
    for key, value in changes.items():
        setattr(row, key, value)
    if "status" in changes and changes["status"] in ("untracked", "skipped", "withdrawn") and "stage" not in changes:
        row.stage = None
    row.revision += 1
    row.updated_at = datetime.now(UTC)
    s.flush()
    s.add(JobFollowupEvent(job_id=job_id, request_key=request_key, before=before, after=state(row),
                           changes=encoded_changes, source_text=source_text))
    s.flush()
    out = get(s, job_id, history=True)
    if row.status == "rejected" and row.note and ("note" in changes or changes.get("status") == "rejected"):
        out["memory_event"] = rejection_to_memory(s, s.get(Job, job_id), row.note, row.updated_at)
    return out


def context(s: Session) -> str:
    rows = s.execute(select(JobFollowup, Job).join(Job, Job.id == JobFollowup.job_id)
                     .order_by(JobFollowup.updated_at.desc()).limit(50)).all()
    return "\n".join(f"- J-{j.id} {j.company} · {j.title}：{'已收藏；' if f.saved else ''}{STATUSES[f.status]}"
                      + (f"；{f.stage}" if f.stage else "") + (f"；下次跟进 {f.next_followup}" if f.next_followup else "")
                      + (f"；用户备注 {f.note}" if f.note else "") for f, j in rows)


STALE_DAYS = 7  # 投递或面试后这么多天没有新进展、也没定跟进日期，就提醒一次
DRAFT_PROMPT_VERSION = "followup-draft-v2"  # v2：卡片里没有的到岗信息逐项列为占位（演示里编出“满足 3 个月”）
SCENARIOS = {
    "untracked": "还没投递：写给 HR 或内推人，礼貌询问 JD 里没写清的事项，并简短说明自己的情况",
    "skipped": "还没投递：写给 HR 或内推人，礼貌询问 JD 里没写清的事项，并简短说明自己的情况",
    "applied": "已投递但还没消息：询问简历进度，说明每周到岗天数、实习时长和最早到岗时间，附作品链接",
    "interview": "面试中：感谢面试官或 HR，询问下一步安排，必要时补充一两句和岗位相关的项目亮点",
    "rejected": "已被拒：礼貌感谢，请对方方便时给一两点改进建议，表达以后有合适机会还想再试",
    "offer": "已拿到 Offer：确认入职时间、每周到岗天数、实习时长等细节",
    "withdrawn": "已撤回：简短礼貌说明情况，感谢对方的时间",
}
CARD_KEYS = ("school", "degree", "major", "grad_year", "available_from", "days_per_week", "duration_months",
             "project", "skill", "rejection_feedback")


def stale(s: Session, now: datetime | None = None) -> list[tuple[JobFollowup, Job, int]]:
    """投递或面试后 STALE_DAYS 天没动静、也没自己定跟进日期的岗位：(进度, 岗位, 已过天数)。"""
    now = now or datetime.now(UTC)
    rows = s.execute(select(JobFollowup, Job).join(Job, Job.id == JobFollowup.job_id)
                     .where(JobFollowup.status.in_(("applied", "interview")), JobFollowup.next_followup.is_(None))
                     .order_by(JobFollowup.updated_at)).all()
    return [(f, j, (now - f.updated_at).days) for f, j in rows if (now - f.updated_at).days >= STALE_DAYS]


def rejection_to_memory(s: Session, job: Job, reason: str, now: datetime) -> dict[str, Any] | None:
    """标成“未通过”并写了理由：记进卡片的“被拒的反馈”，之后推荐和面试准备会针对这些短板。"""
    from polaris.memory.update import Candidate, Context, apply

    reason = reason.strip()
    if not reason:
        return None
    qualifier = f"{job.company or job.company_name} | {job.title}"
    event, _ = apply(Context(session=s, now=now, source_text=f"用户在岗位跟进里记下的被拒理由：{reason}"),
                     Candidate(attribute="rejection_feedback", value=reason[:500], qualifier=qualifier,
                               statement=f"{qualifier} 没通过，反馈：{reason[:200]}", job_ref=f"J-{job.id}"))
    return event


def draft(s: Session, job_id: int, llm: Any = None) -> dict[str, Any]:
    """按当前进度起草一条跟进消息。只起草，不发送；用户确认、修改后自己发。"""
    from polaris.llm.client import LLM
    from polaris.match import matches
    from polaris.memory.schema import ATTRIBUTES
    from polaris.memory.update import card
    from polaris.tools.jobs import job_sources

    job = s.get(Job, job_id)
    if job is None:
        raise LookupError("岗位不存在")
    follow = get(s, job_id)
    row = s.scalar(select(JobFollowup).where(JobFollowup.job_id == job_id))
    days = (datetime.now(UTC) - row.updated_at).days if row else None
    fit = matches(s, [job_id]).get(job_id) or {}
    asks = [c["detail"] for c in fit.get("checks", []) if c.get("ok") is None]  # JD 没写清、值得问一句的事
    facts = [f for f in card(s) if f.attribute in CARD_KEYS]
    known = {f.attribute for f in facts}
    missing = [ATTRIBUTES[k].label for k in ("days_per_week", "duration_months", "available_from") if k not in known]
    me = "\n".join(f"- {ATTRIBUTES[f.attribute].label}{f'（{f.qualifier}）' if f.qualifier else ''}：{f.value}"
                   for f in facts) or "（卡片里还没有背景信息）"
    from polaris.config import get_settings

    link = get_settings().portfolio_url or "[作品链接]"
    sources = job_sources(s, [job_id])[job_id]
    scenario = SCENARIOS[follow["status"]]
    prompt = (
        f"岗位：{job.company or job.company_name} · {job.title}（{job.city or '城市未写'}）\n"
        f"岗位要求摘录：{'；'.join((job.requirements or [])[:6]) or '未抽取到'}\n"
        f"当前进度：{follow['status_label']}" + (f"，{follow['stage']}" if follow["stage"] else "")
        + (f"，距上次更新 {days} 天" if days is not None else "") + "\n"
        + (f"用户备注：{follow['note']}\n" if follow["note"] else "")
        + f"JD 没写清、可以顺带问的：{'；'.join(asks) or '无'}\n"
        f"作品链接：{link}\n用户卡片：\n{me}\n\n"
        f"场景：{scenario}。\n"
        "写一条发给对方的中文消息，150 字以内，开头不要寒暄套话，语气礼貌、具体。"
        + (f"卡片里没有：{'、'.join(missing)}。这些只能写成 [请填写…] 占位，不能写“满足要求”“可以”“尽快”。\n"
           if missing else "")
        + "只能用上面给出的事实；卡片里没有的信息（姓名、学校等）用 [方括号] 占位让用户自己填，不要编。"
        "不要承诺用户没说过的事，不要评价自己是否符合岗位要求。只输出消息正文。")
    out = (llm or LLM()).chat([{"role": "user", "content": prompt}], purpose="followup_draft",
                              prompt_version=DRAFT_PROMPT_VERSION, temperature=0.3)
    return {"text": (out.content or "").strip(), "scenario": scenario, "status": follow["status"], "asks": asks,
            "missing": missing,
            "portfolio_url": get_settings().portfolio_url, "sources": [x["url"] for x in sources[:2] if x.get("url")],
            "llm_call_id": out.call_id}


FUNNEL = (("saved", "收藏"), ("applied", "已投递"), ("replied", "有回复"), ("interview", "进面试"), ("offer", "拿到 Offer"))


def funnel(s: Session, now: datetime | None = None) -> dict[str, Any]:
    """投递漏斗：每个岗位按“走到过哪一步”算，不只看现在的状态（面试后被拒也算进过面试、有回复）。"""
    from polaris.db.models import MemoryFact, Posting, RawItem
    from polaris.sources import label

    now = now or datetime.now(UTC)
    reached: dict[int, set[str]] = {}
    applied_at: dict[int, datetime] = {}
    for e in s.scalars(select(JobFollowupEvent).order_by(JobFollowupEvent.id)):
        steps = reached.setdefault(e.job_id, set())
        after = e.after or {}
        if after.get("saved"):
            steps.add("saved")
        status = after.get("status")
        if status in ("applied", "interview", "offer", "rejected"):
            steps.add("applied")
            applied_at.setdefault(e.job_id, e.created_at)
        if status in ("interview", "offer", "rejected"):
            steps.add("replied")
        if status in ("interview", "offer"):
            steps.add("interview")
        if status == "offer":
            steps.add("offer")
    counts = {key: sum(key in steps for steps in reached.values()) for key, _ in FUNNEL}
    applied = counts["applied"]

    def rate(n: int) -> float | None:
        return round(n / applied, 3) if applied else None

    channel_of: dict[int, str] = {}
    ids = [j for j, steps in reached.items() if "applied" in steps]
    for job_id, source in s.execute(select(Posting.job_id, RawItem.source).join(RawItem, RawItem.id == Posting.raw_item_id)
                                    .where(Posting.job_id.in_(ids)).order_by(Posting.id)) if ids else []:
        channel_of.setdefault(job_id, label(source))
    channels: dict[str, dict[str, int]] = {}
    for job_id in ids:
        row = channels.setdefault(channel_of.get(job_id, "来源未知"), {"applied": 0, "replied": 0, "interview": 0})
        for key in row:
            row[key] += key in reached[job_id]
    weeks = []
    for back in range(3, -1, -1):
        start = (now - timedelta(days=now.weekday() + 7 * back)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
        weeks.append({"week": start.date().isoformat(), "applied": sum(start <= t < end for t in applied_at.values())})
    reasons = [{"for": f.qualifier, "reason": f.value} for f in s.scalars(
        select(MemoryFact).where(MemoryFact.attribute == "rejection_feedback", MemoryFact.status == "active")
        .order_by(MemoryFact.recorded_at.desc()).limit(10))]
    return {"steps": [{"key": key, "label": name, "count": counts[key]} for key, name in FUNNEL],
            "rates": {"reply": rate(counts["replied"]), "interview": rate(counts["interview"]), "offer": rate(counts["offer"])},
            "channels": [{"channel": k, **v} for k, v in sorted(channels.items(), key=lambda kv: -kv[1]["applied"])],
            "weeks": weeks, "rejections": reasons,
            "note": "按每个岗位走到过的最远一步统计：标过面试中、之后被拒也算进过面试。只统计你在北极星里记下的进度。"}
