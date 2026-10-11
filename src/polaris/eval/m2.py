"""M2 指标：工具调用评测套件 v0（每题跑 k 次，算 pass^k）、出错恢复、追踪完整率、回放一致性。

结果写到 eval/reports/M2.md 和 M2.json；第一次运行的录像存到 eval/cassettes/m2/，之后可以不花钱地完整回放。
"""
import json
from typing import Any

from polaris.agent.minimal import MINIMAL
from polaris.config import ROOT
from polaris.eval.runner import CATEGORY_NAMES, run_suite
from polaris.eval.suites.tool_calling import SUITE_VERSION, build_tasks

REPORT_DIR = ROOT / "eval" / "reports"
CASSETTE_DIR = ROOT / "eval" / "cassettes" / "m2"


def pct(n: float, d: float) -> str:
    return f"{n / d:.0%}" if d else "—"


def run(k: int = 3) -> dict[str, Any]:
    stats = run_suite(build_tasks(), k=k, module="M2", suite=SUITE_VERSION, cassette_dir=CASSETTE_DIR, profile=MINIMAL)
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
        "满分不说明模型很强。（10-11 更正：原先预计换成真实数据后分数会下来，实际 M3 的真实数据套件 real-v1 仍是满分，"
        "说明题目还不够难；能拉开差距的题——多跳对比、跨来源冲突、按时间筛、推荐类开放题——放进后面的 Polaris-Bench。）",
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
