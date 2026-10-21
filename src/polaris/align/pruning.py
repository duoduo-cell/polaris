"""派生岗位清理许可：固定对象及版本，人工记录永不由该入口清理。"""
import hashlib
import json
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, defer

from polaris.db.models import Job, JobFollowup, JobFollowupEvent, Posting


class PruneBlocked(RuntimeError):
    def __init__(self, reason: str, count: int):
        self.reason, self.count = reason, count
        super().__init__(f"prune blocked: {reason} ({count} jobs); diagnose and review again")


def stale_condition(keys):
    # NULL key 没有参与自动对齐，即使输入为空也不能扩大管理范围。
    return Job.key.is_not(None) & Job.key.not_in(keys)


def candidates(s: Session, keys, *, lock: bool = False) -> list[Job]:
    stmt = select(Job).options(defer(Job.embedding)).where(stale_condition(keys)).order_by(Job.id)
    if lock:
        stmt = stmt.with_for_update()
    return list(s.scalars(stmt))


def fingerprint(job: Job, posting_ids: list[int]) -> str:
    # 包括 updated_at 和所有业务列；向量是派生值，不作为清理许可的内容。
    values = {c.name: getattr(job, c.name) for c in Job.__table__.columns if c.name != "embedding"}
    values["linked_posting_ids"] = sorted(posting_ids)
    payload = json.dumps(values, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def manifest(s: Session, jobs: list[Job]) -> dict[str, str]:
    links = {j.id: [] for j in jobs}
    if links:
        for pid, jid in s.execute(select(Posting.id, Posting.job_id).where(Posting.job_id.in_(links))):
            links[jid].append(pid)
    return {str(job.id): fingerprint(job, links[job.id]) for job in jobs}


def protected_ids(s: Session, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    return set(s.scalars(select(Job.id).where(Job.id.in_(ids), or_(
        select(JobFollowup.id).where(JobFollowup.job_id == Job.id).exists(),
        select(JobFollowupEvent.id).where(JobFollowupEvent.job_id == Job.id).exists()))))


def authorize(s: Session, jobs: list[Job], approval: dict[str, Any] | None) -> None:
    if not jobs:
        return
    # candidates 已 FOR UPDATE，阻止并发 FK 插入在检查后又悄悄被 CASCADE。
    protected = protected_ids(s, [j.id for j in jobs])
    if protected:
        raise PruneBlocked("user_history", len(protected))
    if not isinstance(approval, dict):
        raise PruneBlocked("approval_required", len(jobs))
    current = manifest(s, jobs)
    changed = [j for j in jobs if approval.get(str(j.id)) != current[str(j.id)]]
    if changed:
        raise PruneBlocked("scope_changed", len(changed))
