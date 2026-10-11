"""具体岗位的收藏和进度。只有用户明确操作才写入，保留历史、版本和幂等请求。"""
from datetime import UTC, datetime
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
    return get(s, job_id, history=True)


def context(s: Session) -> str:
    rows = s.execute(select(JobFollowup, Job).join(Job, Job.id == JobFollowup.job_id)
                     .order_by(JobFollowup.updated_at.desc()).limit(50)).all()
    return "\n".join(f"- J-{j.id} {j.company} · {j.title}：{'已收藏；' if f.saved else ''}{STATUSES[f.status]}"
                      + (f"；{f.stage}" if f.stage else "") + (f"；下次跟进 {f.next_followup}" if f.next_followup else "")
                      + (f"；用户备注 {f.note}" if f.note else "") for f, j in rows)
