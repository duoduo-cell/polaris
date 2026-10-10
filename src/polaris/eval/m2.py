"""M2 指标：工具调用评测套件 v0（每题跑 k 次，算 pass^k）、出错恢复、追踪完整率、回放一致性。

结果写到 eval/reports/M2.md 和 M2.json；第一次运行的录像存到 eval/cassettes/m2/，之后可以不花钱地完整回放。
"""
import json
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from polaris.agent.minimal import PROMPT_VERSION, ask
from polaris.config import ROOT, get_settings
from polaris.db.models import LLMCall, ToolCall
from polaris.db.session import session_scope
from polaris.eval.suites.tool_calling import SUITE_VERSION, Task, build_tasks, check
from polaris.llm import parallel_map
from polaris.replay import Cassette
from polaris.runs import git_commit, run_scope

REPORT_DIR = ROOT / "eval" / "reports"
CASSETTE_DIR = ROOT / "eval" / "cassettes" / "m2"
CATEGORY_NAMES = {"lookup": "单查", "english": "英文", "conflict": "冲突识别", "irrelevant": "不该调工具",
                  "abstain": "找不到要说找不到", "recovery": "出错恢复"}


def pct(n: float, d: float) -> str:
    return f"{n / d:.0%}" if d else "—"


def percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def run_one(task: Task, k: int, run_id: int) -> dict[str, Any]:
    cassette = Cassette(CASSETTE_DIR / f"{task.id}.jsonl", "record") if k == 0 else None
    if cassette is not None:
        cassette.record_meta({"task": task.id, "category": task.category, "question": task.question,
                              "faults": task.faults, "suite": SUITE_VERSION})
    r = ask(task.question, run_id=run_id, thread_id=f"{task.id}#{k}", cassette=cassette, faults=task.faults,
            purpose="m2_tool_calling")
    if cassette is not None and r.error is None:
        cassette.save()
    result = check(task, r.answer, r.tool_calls, r.error)
    return {"task": task.id, "category": task.category, "k": k, "answer": r.answer, "error": r.error,
            "latency_ms": r.latency_ms, "tools": [c["tool"] for c in r.tool_calls], **result}


def replay_one(task: Task, original: str) -> bool:
    path = CASSETTE_DIR / f"{task.id}.jsonl"
    if not path.exists():
        return False
    r = ask(task.question, cassette=Cassette(path, "replay", llm_replay=True), faults=task.faults, record_db=False)
    return r.error is None and r.answer == original


def run(k: int = 3) -> dict[str, Any]:
    tasks = build_tasks()
    by_id = {t.id: t for t in tasks}
    with run_scope("eval", {"module": "M2", "suite": SUITE_VERSION, "k": k, "prompt_version": PROMPT_VERSION}) as (
            run_id, stats):
        jobs = [(t, i) for i in range(k) for t in tasks]
        raw = parallel_map(lambda job: run_one(job[0], job[1], run_id), jobs, get_settings().llm_concurrency)
        results = [r if isinstance(r, dict) else {"task": j[0].id, "category": j[0].category, "k": j[1],
                                                   "answer": "", "error": repr(r), "success": False, "latency_ms": 0,
                                                   "tools": [], "n_tool_calls": 0, "invalid_args": 0, "injected": 0,
                                                   "values_ok": False, "cite_ok": False, "tools_ok": False}
                   for j, r in zip(jobs, raw, strict=True)]

        firsts = {r["task"]: r["answer"] for r in results if r["k"] == 0 and not r["error"]}
        replay_ok = {tid: replay_one(by_id[tid], ans) for tid, ans in firsts.items()}

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
            "suite": SUITE_VERSION, "prompt_version": PROMPT_VERSION, "k": k,
            "model": get_settings().model_for("chat"),
            "tasks": len(tasks), "runs": len(results), "success": sum(r["success"] for r in results),
            "pass_k": sum(all(v) for v in per_task.values()), "categories": cats,
            "tool_calls": len(tool_rows), "invalid_args": sum(c.error_kind == "invalid_args" for c in tool_rows),
            "tool_errors": sum(c.error_kind == "tool_error" for c in tool_rows),
            "injected": sum(c.error_kind == "injected" for c in tool_rows),
            "arg_valid_rate": (1 - sum(c.error_kind == "invalid_args" for c in non_injected) / len(non_injected))
            if non_injected else None,
            "avg_tool_calls": statistics.mean(r["n_tool_calls"] for r in results) if results else None,
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
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "M2.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2, default=str),
                                            encoding="utf-8", newline="\n")
        (REPORT_DIR / "M2.md").write_text(render(stats), encoding="utf-8", newline="\n")
    return stats


def render(s: dict[str, Any]) -> str:
    lines = [
        "# M2 指标：工具层和 agent 底座",
        "",
        f"生成时间 {s['generated_at']}，代码版本 `{s['git_commit']}`，模型 `{s['model']}`，"
        f"提示词 `{s['prompt_version']}`，套件 `{s['suite']}`。",
        "",
        "开发期用的是第三方 OpenAI 兼容服务上的模型，这里的数字说明底座工作正常；正式指标以提交时的模型为准。",
        "",
        "**这一版题目偏简单**：合成数据、只有 2 个工具、问题直接。它验证的是工具层、追踪、故障注入和录制回放这套底座，"
        "满分不说明模型很强。M3 起换成小红书、Boss 和大厂官网的真实数据和更多工具，分数会下来，那才是真正衡量 agent 的。",
        "",
        f"## 工具调用套件（{s['tasks']} 题，每题跑 {s['k']} 次）",
        "",
        "按最终结果评分：回答里有没有正确的值、有没有写出依据的条目编号、该不该调工具。"
        f"pass^{s['k']} 指同一题 {s['k']} 次全部成功。",
        "",
        f"| 类别 | 题数 | 单次成功率 | pass^{s['k']} |",
        "|---|---|---|---|",
    ]
    for cat, c in s["categories"].items():
        lines.append(f"| {CATEGORY_NAMES[cat]} | {c['tasks']} | {c['success']}/{c['runs']}（{pct(c['success'], c['runs'])}） | "
                     f"{c['pass_k']}/{c['tasks']} |")
    lines += [
        f"| **合计** | {s['tasks']} | {s['success']}/{s['runs']}（{pct(s['success'], s['runs'])}） | "
        f"**{s['pass_k']}/{s['tasks']}（{pct(s['pass_k'], s['tasks'])}）** |",
        "",
        "## 工具层和追踪",
        "",
        "| 指标 | 结果 |",
        "|---|---|",
        f"| 工具调用次数（含注入的故障） | {s['tool_calls']} |",
        f"| 参数一次合格率（不含注入的故障） | {pct(s['arg_valid_rate'] or 0, 1) if s['arg_valid_rate'] is not None else '—'} |",
        f"| 工具报错（如编号不存在） | {s['tool_errors']} 次 |",
        f"| 注入的故障 | {s['injected']} 次 |",
        f"| 平均每题工具调用 | {s['avg_tool_calls']:.1f} 次 |" if s["avg_tool_calls"] is not None else "| 平均每题工具调用 | — |",
        f"| 每题耗时中位数 / 第 95 百分位 | {s['latency_p50_ms']} ms / {s['latency_p95_ms']} ms |",
        f"| 模型调用追踪完整率 | {s['llm_trace_complete']}/{s['llm_answered']}（{pct(s['llm_trace_complete'], s['llm_answered'])}） |",
        f"| token（输入 / 输出） | {s['prompt_tokens']} / {s['completion_tokens']} |",
        f"| 完整回放与原次回答一致 | {s['replay_identical']}/{s['replay_total']} |",
        "",
    ]
    if s["failures"]:
        lines += ["## 没通过的", "", "| 题 | 第几次 | 值对 | 编号对 | 工具对 | 回答开头 |", "|---|---|---|---|---|---|"]
        for f in s["failures"]:
            ans = (f["error"] or f["answer"]).replace("\n", " ").replace("|", "／")[:80]
            lines.append(f"| {f['task']} | {f['k'] + 1} | {'是' if f['values_ok'] else '否'} | "
                         f"{'是' if f['cite_ok'] else '否'} | {'是' if f['tools_ok'] else '否'} | {ans} |")
        lines.append("")
    lines += ["复现：`uv run polaris metrics m2`（会调模型）；录像在 `eval/cassettes/m2/`，"
              "`uv run polaris agent ask \"问题\" --replay eval/cassettes/m2/<题>.jsonl --full` 可不花钱回放。", ""]
    return "\n".join(lines)
