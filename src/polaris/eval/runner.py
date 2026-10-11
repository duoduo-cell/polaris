"""评测套件的通用跑法：每题跑 k 次算 pass^k，第一次录像，之后完整回放核对一致性，汇总工具层和追踪指标。

M2（合成数据、raw_items 工具）和 M3（真实数据、jobs/posts 工具）共用；区别只在题目、工具配置和录像目录。
"""
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from polaris.agent.minimal import MINIMAL, Profile, ask
from polaris.config import get_settings
from polaris.db.models import LLMCall, ToolCall
from polaris.db.session import session_scope
from polaris.eval.suites.tool_calling import Task, check
from polaris.llm import parallel_map
from polaris.replay import Cassette
from polaris.runs import git_commit, run_scope

CATEGORY_NAMES = {"lookup": "单查", "english": "英文", "conflict": "冲突识别", "irrelevant": "不该调工具",
                  "abstain": "找不到要说找不到", "recovery": "出错恢复", "posts": "查笔记", "alias": "公司别名",
                  "multi": "多条件"}


def percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def run_one(task: Task, k: int, run_id: int, cassette_dir: Path, suite: str, profile: Profile = MINIMAL,
            purpose: str = "m2_tool_calling") -> dict[str, Any]:
    cassette = Cassette(cassette_dir / f"{task.id}.jsonl", "record") if k == 0 else None
    if cassette is not None:
        cassette.record_meta({"task": task.id, "category": task.category, "question": task.question,
                              "faults": task.faults, "suite": suite, "profile": profile.name})
    r = ask(task.question, run_id=run_id, thread_id=f"{task.id}#{k}", cassette=cassette, faults=task.faults,
            purpose=purpose, profile=profile)
    if cassette is not None and r.error is None:
        cassette.save()
    result = check(task, r.answer, r.tool_calls, r.error)
    return {"task": task.id, "category": task.category, "k": k, "answer": r.answer, "error": r.error,
            "latency_ms": r.latency_ms, "tools": [c["tool"] for c in r.tool_calls], **result}


def replay_one(task: Task, original: str, cassette_dir: Path, profile: Profile = MINIMAL) -> bool:
    path = cassette_dir / f"{task.id}.jsonl"
    if not path.exists():
        return False
    r = ask(task.question, cassette=Cassette(path, "replay", llm_replay=True), faults=task.faults, record_db=False,
            profile=profile)
    return r.error is None and r.answer == original


def run_suite(tasks: list[Task], *, k: int, module: str, suite: str, cassette_dir: Path, profile: Profile = MINIMAL,
              purpose: str = "m2_tool_calling") -> dict[str, Any]:
    by_id = {t.id: t for t in tasks}
    with run_scope("eval", {"module": module, "suite": suite, "k": k, "prompt_version": profile.prompt_version}) as (
            run_id, stats):
        jobs = [(t, i) for i in range(k) for t in tasks]
        raw = parallel_map(lambda job: run_one(job[0], job[1], run_id, cassette_dir, suite, profile, purpose), jobs,
                           get_settings().llm_concurrency)
        results = [r if isinstance(r, dict) else {"task": j[0].id, "category": j[0].category, "k": j[1],
                                                   "answer": "", "error": repr(r), "success": False, "latency_ms": 0,
                                                   "tools": [], "n_tool_calls": 0, "invalid_args": 0, "injected": 0,
                                                   "values_ok": False, "cite_ok": False, "tools_ok": False}
                   for j, r in zip(jobs, raw, strict=True)]

        firsts = {r["task"]: r["answer"] for r in results if r["k"] == 0 and not r["error"]}
        replay_ok = {tid: replay_one(by_id[tid], ans, cassette_dir, profile) for tid, ans in firsts.items()}

        with session_scope() as s:
            llm = s.execute(select(LLMCall).where(LLMCall.run_id == run_id)).scalars().all()
            tool_rows = s.execute(select(ToolCall).where(ToolCall.run_id == run_id)).scalars().all()
        answered = [c for c in llm if not c.error]
        per_task: dict[str, list[bool]] = defaultdict(list)
        for r in results:
            per_task[r["task"]].append(r["success"])
        cats: dict[str, dict[str, Any]] = {}
        for cat in CATEGORY_NAMES:
            rs = [r for r in results if r["category"] == cat]
            ts = [t.id for t in tasks if t.category == cat]
            if rs:
                cats[cat] = {"runs": len(rs), "success": sum(r["success"] for r in rs),
                             "tasks": len(ts), "pass_k": sum(all(per_task[t]) for t in ts)}
        non_injected = [c for c in tool_rows if c.error_kind != "injected"]
        latencies = [r["latency_ms"] for r in results if not r["error"]]
        stats.update({
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "git_commit": git_commit(),
            "suite": suite, "prompt_version": profile.prompt_version, "k": k,
            "model": get_settings().model_for("chat"),
            "tasks": len(tasks), "runs": len(results), "success": sum(r["success"] for r in results),
            "pass_k": sum(all(v) for v in per_task.values()), "categories": cats,
            "tool_calls": len(tool_rows), "invalid_args": sum(c.error_kind == "invalid_args" for c in tool_rows),
            "tool_errors": sum(c.error_kind == "tool_error" for c in tool_rows),
            "injected": sum(c.error_kind == "injected" for c in tool_rows),
            "arg_valid_rate": (1 - sum(c.error_kind == "invalid_args" for c in non_injected) / len(non_injected))
            if non_injected else None,
            "avg_tool_calls": statistics.mean(r["n_tool_calls"] for r in results) if results else None,
            "tool_usage": dict(sorted(Counter(c.tool for c in tool_rows).items())),
            "latency_p50_ms": percentile(latencies, 0.5), "latency_p95_ms": percentile(latencies, 0.95),
            "llm_calls": len(llm), "llm_answered": len(answered),
            "llm_trace_complete": sum(1 for c in answered if c.prompt_tokens is not None and c.completion_tokens is not None),
            "prompt_tokens": sum(c.prompt_tokens or 0 for c in answered),
            "completion_tokens": sum(c.completion_tokens or 0 for c in answered),
            "replay_identical": sum(replay_ok.values()), "replay_total": len(replay_ok),
            "failures": [{k_: r[k_] for k_ in ("task", "k", "values_ok", "cite_ok", "tools_ok", "error")}
                         | {"answer": r["answer"][:200]} for r in results if not r["success"]],
            "results": results,
        })
    return dict(stats)
