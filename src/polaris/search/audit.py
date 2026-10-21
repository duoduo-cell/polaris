"""知识层历史积压诊断：只读一致快照；恢复显式排队，不执行采集或模型。"""
from datetime import UTC, date, datetime
from time import perf_counter
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import defer

from polaris.align.build import alignment_rows, load_views, official_snapshots
from polaris.db.models import Company, InterviewQuestion, Job, Post, Posting, RawItem, Run, SearchChunk
from polaris.db.session import session_scope
from polaris.interviews import profile
from polaris.search import maintenance
from polaris.search.chunking import CHUNK_FIELDS, chunk_values
from polaris.search.index import build_chunks, embedding_pending


def _value(value: Any) -> Any:
    # Job.posted_at 是 date，而块 published_at 是 timestamptz：按日期零点比较。
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat() + "T00:00:00+00:00"
    if isinstance(value, tuple):
        return list(value)
    return value


def _diff(expected: dict[str, dict[str, Any]], actual: dict[str, Any]) -> dict[str, int]:
    return {"missing": len(expected.keys() - actual.keys()),
            "changed": sum(any(_value(getattr(actual[k], f)) != _value(v) for f, v in expected[k].items())
                           for k in expected.keys() & actual.keys()),
            "stale": len(actual.keys() - expected.keys())}


def diagnose() -> dict[str, Any]:
    """全量只读检查，不宣称召回质量／来源真实性；不加载向量或输出原文。"""
    started = perf_counter()
    with session_scope() as s:
        s.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        s.execute(text("SET LOCAL statement_timeout = '30s'"))
        snapshot_at = s.scalar(select(func.transaction_timestamp())).isoformat()
        run = s.scalar(select(Run).where(Run.kind == maintenance.KIND).order_by(Run.id.desc()).limit(1))
        task = {"status": "idle"} if run is None else {"run_id": run.id, "status": run.status}
        raw_pending = {}
        for kind, model in (("job", Posting), ("post", Post)):
            count, oldest = s.execute(select(func.count(), func.min(RawItem.created_at)).where(
                RawItem.kind == kind, ~select(model.raw_item_id).where(model.raw_item_id == RawItem.id).exists())).one()
            raw_pending[kind] = {"count": count, "oldest_at": oldest.isoformat() if oldest else None}

        jobs = list(s.scalars(select(Job).options(defer(Job.embedding))))
        actual_jobs = {j.key: j for j in jobs if j.key is not None}
        company_ids = dict(s.execute(select(Company.name, Company.id)).all())
        planned = alignment_rows(load_views(s), official_snapshots(s), company_ids)
        expected_jobs = {row["key"]: row for row, _ in planned}
        delta = _diff(expected_jobs, actual_jobs)
        links = dict(s.execute(select(Posting.id, Posting.job_id)).all())
        mislinked = sum(links[pid] != (actual_jobs[row["key"]].id if row["key"] in actual_jobs else None)
                        or row["key"] not in actual_jobs for row, ids in planned for pid in ids)
        # 与 run_align 的 NOT IN 完全一致：非空集合不匹配 NULL；空集合则会清理所有岗位。
        stale_jobs = s.scalar(select(func.count()).select_from(Job).where(Job.key.not_in(expected_jobs)))
        alignment = {"missing_jobs": delta["missing"], "changed_jobs": delta["changed"],
                     "stale_jobs": stale_jobs, "mislinked_postings": mislinked}
        unmanaged_jobs = sum(j.key is None for j in jobs) if expected_jobs else 0

        expected_chunks = {c.key: chunk_values(c) for c in build_chunks(s)}
        # 向量可能很大，诊断只加载检索字段，并在 SQL 统计不可用的向量。
        fields = [getattr(SearchChunk, f) for f in CHUNK_FIELDS]
        chunks = _diff(expected_chunks, {r.key: r for r in s.execute(select(*fields))})
        unavailable = s.scalar(select(func.count()).select_from(SearchChunk).where(embedding_pending()))

        expected_questions = {}
        for p, raw in s.execute(select(Post, RawItem).join(RawItem, RawItem.id == Post.raw_item_id)):
            expected_questions.update({q["key"]: q for q in profile(p, raw.raw_text, raw.raw_json)["questions"]})
        questions = _diff(expected_questions, {q.key: q for q in s.scalars(select(InterviewQuestion))})
        return {"snapshot_at": snapshot_at, "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                "task": task, "raw_pending": raw_pending, "alignment": alignment, "chunks": chunks,
                "questions": questions, "embeddings": {"unavailable": unavailable}, "unmanaged_jobs": unmanaged_jobs,
                "needs_refresh": bool(any(alignment.values()) or any(chunks.values()) or any(questions.values()) or unavailable),
                "limitations": ["全量快照检查，单条 SQL 超时 30 秒，不保证整次耗时上限；快照后数据可能变化。",
                                "复用当前对齐／切块／题目规则，不验证抽取准确性、提示词版本、来源真实性或实际检索质量。",
                                "原文未抽取不由知识 worker 处理；不检查可选图谱、向量模型迁移或向量数值质量。",
                                "排队沿用全量对齐清理：无分组时清理全部岗位；有分组时 NULL key 不清理，单列 unmanaged_jobs。",
                                "stale_jobs 是当前快照计数，不是执行时删除清单；排队后数据变化与直接入口并发仍须处理。"]}


def enqueue_refresh(*, allow_prune: bool = False) -> dict[str, Any]:
    """诊断后显式登记全量知识待办；不执行，不复位耗尽，不重复清除活跃检查点。"""
    diagnosis = diagnose()
    with maintenance._state() as (s, run):
        state = run.status if run else "idle"
        if state == "exhausted":
            return {"queued": False, "reason": "exhausted", "diagnosis": diagnosis}
        if run is not None and state != "ok":
            return {"queued": False, "reason": "already_pending", "run_id": run.id, "diagnosis": diagnosis}
        if not diagnosis["needs_refresh"]:
            return {"queued": False, "reason": "no_detected_backlog", "diagnosis": diagnosis}
        if diagnosis["alignment"]["stale_jobs"] and not allow_prune:
            return {"queued": False, "reason": "prune_requires_acknowledgement", "diagnosis": diagnosis}
        run_id = maintenance.request_refresh("manual:historical-backlog", session=s)
        return {"queued": True, "run_id": run_id, "diagnosis": diagnosis}
