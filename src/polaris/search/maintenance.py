"""采集后的有限知识补跑。Run 保存进度；不拥有浏览器，不是通用任务引擎。

状态锁只包围短事务；执行锁用 PostgreSQL 会话锁，退出进程后自动释放。
步骤允许至少一次执行：提交数据后、保存检查点前崩溃，仍依赖原步骤的幂等性。
"""
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from polaris.db.models import Run
from polaris.db.session import get_engine, session_scope
from polaris.runs import git_commit

KIND = "knowledge_refresh"
STATE_LOCK = 2026102101
WORKER_LOCK = 2026102102
MAX_ATTEMPTS = 3  # 初次加两次补跑；每个核心步骤独立计数
RETRY_DELAY = timedelta(minutes=5)
EMBED_LIMIT = 200  # 一次维护调用最多 4 组；对齐、切块仍沿用全量实现
STAGES = ("align", "chunks", "embed", "graph")


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC)


@contextmanager
def _state(session: Session | None = None) -> Iterator[tuple[Session, Run | None]]:
    if session is None:
        with session_scope() as s, _state(s) as state:
            yield state
        return
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": STATE_LOCK})
    yield session, session.scalar(select(Run).where(Run.kind == KIND).order_by(Run.id.desc()).limit(1))


def _view(run: Run) -> dict[str, Any]:
    return {"run_id": run.id, "status": run.status, "params": run.params, "stats": run.stats}


def status() -> dict[str, Any]:
    """只读；完成标记是处理检查点，不等于真实平台／查询质量验收。"""
    with session_scope() as s:
        run = s.scalar(select(Run).where(Run.kind == KIND).order_by(Run.id.desc()).limit(1))
        return _view(run) if run else {"status": "idle"}


def request_refresh(reason: str, *, now: datetime | None = None, session: Session | None = None) -> int:
    """落待办；传入 session 时和业务写入同事务提交，不自行提交或执行。

    新数据不重置失败额度，不能靠不断采集绕过耗尽状态。
    """
    now = _now(now)
    with _state(session) as (s, run):
        if run is None or run.status == "ok":
            run = Run(kind=KIND, status="pending", started_at=now, git_commit=git_commit(),
                      params={"generation": 1, "reason": reason[:100]},
                      stats={"stages": {}, "attempts": {}, "next_at": now.isoformat()})
            s.add(run)
            s.flush()
        else:
            run.params = {**run.params, "generation": run.params["generation"] + 1, "reason": reason[:100]}
            # 执行中收到新数据时，旧代的步骤不能把新代误记完成。冷却与额度保留。
            run.stats = {**run.stats, "stages": {}, "requested_at": now.isoformat()}
            if run.status != "exhausted":
                run.status = "pending"
        return run.id


def retry(*, now: datetime | None = None) -> dict[str, Any]:
    """显式人工重新排队；不执行、不启动 daemon，也不重置正常运行中的额度。"""
    now = _now(now)
    with _state() as (_, run):
        if run is None or run.status != "exhausted":
            return {"queued": False, "status": run.status if run else "idle"}
        run.status, run.finished_at = "pending", None
        run.stats = {**run.stats, "attempts": {}, "next_at": now.isoformat(),
                     "manual_retries": run.stats.get("manual_retries", 0) + 1,
                     "manual_retry_at": now.isoformat()}
        return {"queued": True, **_view(run)}


def _execute(stage: str) -> dict[str, Any]:
    if stage == "align":
        from polaris.align.build import run_align
        return run_align()
    if stage == "chunks":
        from polaris.search.index import sync_chunks
        return sync_chunks()
    if stage == "embed":
        from polaris.search.index import embed_pending
        return embed_pending(limit=EMBED_LIMIT)
    from polaris.graph.client import enabled

    if not enabled():
        return {"skipped": "disabled"}
    from polaris.graph.build import rebuild
    return rebuild()


def _error(stage: str, result: dict[str, Any]) -> str | None:
    if result.get("error"):
        return str(result["error"])[:200]
    if stage == "embed":
        # 缺字段不能被默认为成功；remaining 是工作量限制之后的真实积压。
        for key in ("pending", "embedded", "failed_groups", "remaining"):
            if type(result.get(key)) is not int or result[key] < 0:
                return f"invalid embedding result: {key}"
        if result["failed_groups"]:
            return f"{result['failed_groups']} embedding groups failed: {result.get('errors', [])}"[:200]
        if result["remaining"] and not result["embedded"]:
            return "embedding backlog made no progress"
    return None


def _claim(run_id: int, generation: int, stage: str, now: datetime) -> bool:
    with _state() as (_, run):
        if run is None or run.id != run_id or run.params["generation"] != generation:
            return False
        stats = dict(run.stats)
        attempts = dict(stats["attempts"])
        if attempts.get(stage, 0) >= MAX_ATTEMPTS:
            error = f"{stage}: attempts exhausted (possibly interrupted)"
            run.finished_at = now
            if stage == "graph":
                run.status = "ok"
                run.stats = {**stats, "core_completed_at": now.isoformat(), "graph_error": error,
                             "stages": {**stats["stages"], "graph": {"state": "failed", "result": {"error": error}}}}
            else:
                run.status = "exhausted"
                run.stats = {**stats, "error": error, "failed_stage": stage}
            return False
        attempts[stage] = attempts.get(stage, 0) + 1
        run.status = "running"
        run.stats = {**stats, "attempts": attempts, "current_stage": stage,
                     "next_at": (now + RETRY_DELAY).isoformat()}
        return True


def _record(run_id: int, generation: int, stage: str, result: dict[str, Any],
            error: str | None, now: datetime) -> bool:
    """返回能否继续本次流水线。并发新数据、失败或还有向量积压时结束本次调用。"""
    with _state() as (_, run):
        if run is None or run.id != run_id:
            return False
        stats = dict(run.stats)
        attempts = dict(stats["attempts"])
        if not error:
            attempts[stage] = 0
        if run.params["generation"] != generation:
            # 数据写入可能已完成，仍保守重做新代；成功不消耗新代失败额度。
            run.stats = {**stats, "attempts": attempts}
            return False
        backlog = stage == "embed" and not error and result["remaining"] > 0
        states = {**stats["stages"], stage: {"state": "failed" if error else "pending" if backlog else "ok",
                                           "result": result, "at": now.isoformat()}}
        stats.update(stages=states, attempts=attempts)
        if error and stage != "graph":
            run.status = "exhausted" if attempts[stage] >= MAX_ATTEMPTS else "pending"
            stats.update(error=error, failed_stage=stage, next_at=(now + RETRY_DELAY).isoformat())
            if run.status == "exhausted":
                run.finished_at = now
        elif backlog:
            run.status = "pending"
            stats["next_at"] = (now + RETRY_DELAY).isoformat()
        elif stage == "graph":
            run.status, run.finished_at = "ok", now
            stats.update(core_completed_at=now.isoformat(), graph_error=error)
            stats.pop("error", None)
            stats.pop("failed_stage", None)
        else:
            stats.pop("error", None)
            stats.pop("failed_stage", None)
        run.stats = stats
        return not error and not backlog


def run_pending(*, now: datetime | None = None) -> dict[str, Any]:
    """至多一遍流水线／200 个向量块。失败有界，正常积压可分多遍推进。

    now 仅用于确定性测试；真实执行的阶段时间在执行后读取。无待办时不创建 LLM。
    """
    started = _now(now)
    # 会话锁跨短事务持有；AUTOCOMMIT 避免网络调用期间保留数据库事务快照。
    with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        acquired = conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK})
        if not acquired:
            return {"ran": False, "reason": "busy"}
        try:
            with _state() as (_, run):
                if run is None or run.status in ("ok", "exhausted"):
                    return {"ran": False, "reason": run.status if run else "idle"}
                if datetime.fromisoformat(run.stats["next_at"]) > started:
                    return {"ran": False, "reason": "cooldown"}
                run_id, generation, stages = run.id, run.params["generation"], run.stats["stages"]
            for stage in STAGES:
                if stages.get(stage, {}).get("state") == "ok":
                    continue
                if not _claim(run_id, generation, stage, _now(now)):
                    break
                try:
                    result = _execute(stage)
                    if not isinstance(result, dict):
                        raise TypeError(f"{stage} returned a non-dict result")
                    error = _error(stage, result)
                except Exception as exc:  # noqa: BLE001  中断不伪装成失败，留下已持久化的 running
                    error = f"{type(exc).__name__}: {exc}"[:200]
                    result = {"error": error}
                if not _record(run_id, generation, stage, result, error, _now(now)):
                    break
            return {"ran": True, **status()}
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK})
