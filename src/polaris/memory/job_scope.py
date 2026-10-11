"""用户投递/面试事实的岗位范围；不把公司级进度猜成某个具体岗位。"""
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from polaris.companies import match
from polaris.db.models import ChatMessage, Job, JobFollowup
from polaris.followups import update
from polaris.memory.update import Candidate


def recent_jobs(s: Session, thread_id: str | None) -> list[Job]:
    if not thread_id:
        return []
    messages = s.scalars(select(ChatMessage).where(ChatMessage.thread_id == thread_id, ChatMessage.role == "assistant")
                         .order_by(ChatMessage.id.desc()).limit(5)).all()
    for message in messages:
        ids = [int(r["id"][2:]) for r in (message.turn or {}).get("results", [])
               if r.get("kind") == "job" and re.fullmatch(r"J-\d+", r.get("id", ""))]
        if ids:
            by_id = {j.id: j for j in s.scalars(select(Job).where(Job.id.in_(ids))).all()}
            return [by_id[i] for i in ids if i in by_id]
    return []


def resolve(s: Session, c: Candidate, message: str, recent: list[Job]) -> tuple[Job | None, str | None]:
    if c.attribute not in ("applied", "interview_stage"):
        return None, None
    explicit_ids = re.findall(r"\bJ-(\d+)\b", message)
    named = [s.get(Job, int(i)) for i in explicit_ids]
    if any(j is None for j in named):
        return None, "这个岗位编号不存在，请从刚才的卡片中选择岗位，或提供完整岗位名称。"
    named = [j for j in named if j is not None]
    ordinal = re.search(r"第([一二三四五六七八九1-9])个", message)
    if ordinal:
        value = ordinal[1]
        index = int(value) - 1 if value.isdigit() else "一二三四五六七八九".index(value)
        if index < len(recent):
            named.append(recent[index])
    if not named and len(recent) == 1 and re.search(r"这个岗位|这个职位|刚才那个", message):
        named = recent
    company = match(c.qualifier.split("|")[0]) if c.qualifier else None
    company_name = company.name if company else (c.qualifier or "").split("|")[0].strip()
    candidates = s.scalars(select(Job).where(Job.company == company_name)).all() if company_name else recent
    titled = [j for j in candidates if j.title and j.title in message]
    proposed = list({j.id: j for j in named + titled}.values())
    if company_name:
        proposed = [j for j in proposed if j.company == company_name]
    if len(proposed) == 1:
        job = proposed[0]
    else:
        tracked = s.scalars(select(Job).join(JobFollowup, JobFollowup.job_id == Job.id)
                            .where(Job.company == company_name, JobFollowup.status.in_(("applied", "interview")))).all()
        # 只有一个已投岗位时允许公司简称，两个进度必须追问，不能互相覆盖。
        job = tracked[0] if len(tracked) == 1 and not proposed else None
        if job is None and (len(candidates) > 1 or len(proposed) > 1 or c.job_ref):
            names = "、".join(j.title for j in (proposed or tracked or candidates)[:3])
            return None, f"你说的是{company_name or '哪家公司'}的哪个岗位？{names}。请说岗位名称或编号，进度会分别记录。"
        if job is None and len(candidates) == 1:
            job = candidates[0]
    if job:
        c.qualifier = f"{job.company} | J-{job.id}"
        c.job_ref = f"J-{job.id}"
    return job, None


def sync_followup(s: Session, job: Job, candidate: Candidate, event: dict[str, Any], message: str) -> None:
    if candidate.kind != "stated" or event["op"] not in ("add", "update", "confirm", "invalidate", "noop"):
        return
    changes: dict[str, Any] = {}
    if candidate.attribute == "applied" and re.search(r"投|申请", message):
        changes["status"] = "untracked" if candidate.polarity == "negate" else "applied"
    if candidate.attribute == "interview_stage":
        if candidate.polarity == "negate":
            changes["stage"] = None
        elif not re.search(r"备考|刷题|看面经|准备.*资料|想.*面试", message):
            changes = {"stage": candidate.value, "status": "interview"}
    if not changes:
        return
    row = s.scalar(select(JobFollowup).where(JobFollowup.job_id == job.id))
    # 重复说“我投了”不能把后来的一面状态退回已投递。
    if changes.get("status") == "applied" and row and row.status in ("interview", "offer", "rejected", "withdrawn"):
        changes.pop("status")
    if changes and (row is None or any(getattr(row, k) != v for k, v in changes.items())):
        update(s, job.id, changes, f"memory:{uuid4()}", message)
