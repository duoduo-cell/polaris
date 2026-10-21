"""运行记录：每次采集、入库、生成、评测开头建一条，结束时写指标和状态。"""
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine

from polaris.config import ROOT
from polaris.db.models import Run
from polaris.db.session import session_scope


def git_commit() -> str | None:
    """当前代码版本；有未提交的改动时带 -dirty。Docker 里没有 git，可以用环境变量 GIT_COMMIT 传进来。"""
    if os.environ.get("GIT_COMMIT"):
        return os.environ["GIT_COMMIT"]
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return (head + "-dirty" if dirty else head) or None
    except (OSError, subprocess.SubprocessError):
        return None


@contextmanager
def run_scope(kind: str, params: dict[str, Any] | None = None,
              engine: Engine | None = None) -> Iterator[tuple[int, dict[str, Any]]]:
    """用法：with run_scope("seed") as (run_id, stats): ...；往 stats 里写的指标结束时存进 runs。"""
    with session_scope(engine) as session:
        run = Run(kind=kind, params=params or {}, git_commit=git_commit())
        session.add(run)
        session.flush()
        run_id = run.id
    stats: dict[str, Any] = {}
    status = "ok"
    try:
        yield run_id, stats
    except BaseException as e:
        status = "failed"
        stats["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        with session_scope(engine) as session:
            run = session.get(Run, run_id)
            run.status, run.stats, run.finished_at = status, stats, datetime.now(UTC)
            if kind == "collect" and status == "ok" and (params or {}).get("mode") == "official-daily":
                from polaris.search.maintenance import request_refresh

                # 成功快照影响岗位时效；即使无新原文，也必须和完成状态一起落待办。
                request_refresh("official-snapshot", session=session)
