"""M1 指标：模型调用追踪是否完整、结构化输出和延迟、合成数据核对、入库幂等、缓存。

结果写到 eval/reports/M1.md 和 M1.json，同时记一条 kind=eval 的运行记录。
"""
import json
import statistics
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from polaris.config import ROOT, get_settings
from polaris.db.models import LLMCall, RawItem, Run
from polaris.db.session import session_scope
from polaris.llm import LLM
from polaris.runs import git_commit, run_scope
from polaris.synth.jobs import FIXTURE, read_fixture

REPORT_DIR = ROOT / "eval" / "reports"
CACHE_PROBES = ["只回复一个数字：1+1 等于几？", "只回复一个词：北京是哪个国家的首都？", "只回复 ok"]


def pct(n: int, d: int) -> str:
    return f"{n / d:.1%}" if d else "—"


def percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def synth_stats() -> dict[str, Any]:
    """最近一次成功的合成数据生成：调用追踪、结构化输出、延迟、费用。"""
    with session_scope() as s:
        run = s.execute(select(Run).where(Run.kind == "synth", Run.status == "ok")
                        .order_by(Run.id.desc()).limit(1)).scalar_one_or_none()
        if run is None:
            return {}
        calls = s.execute(select(LLMCall).where(LLMCall.run_id == run.id)).scalars().all()
    answered = [c for c in calls if not c.error or c.error.startswith("validation:")]
    request_errors = [c for c in calls if c.error and not c.error.startswith("validation:")]
    complete = [c for c in answered if c.prompt_tokens is not None and c.completion_tokens is not None
                and c.latency_ms is not None]
    latencies = [c.latency_ms for c in answered if not c.cache_hit]
    costs = [c.cost_usd for c in answered if c.cost_usd is not None]
    return {
        "run_id": run.id, "git_commit": run.git_commit,
        "calls_total": len(calls), "calls_answered": len(answered),
        "trace_complete": len(complete),
        "json_ok": sum(1 for c in answered if c.parsed_ok),
        "json_bad": sum(1 for c in answered if c.parsed_ok is False),
        "request_errors": dict(Counter(c.error.split(":")[0] for c in request_errors)),
        "latency_p50_ms": percentile(latencies, 0.5), "latency_p95_ms": percentile(latencies, 0.95),
        "latency_mean_ms": int(statistics.mean(latencies)) if latencies else None,
        "prompt_tokens": sum(c.prompt_tokens or 0 for c in answered),
        "completion_tokens": sum(c.completion_tokens or 0 for c in answered),
        "reasoning_tokens": sum(c.reasoning_tokens or 0 for c in answered),
        "cost_usd": str(sum(costs, Decimal(0))) if len(costs) == len(answered) and answered else None,
        "models": sorted({c.model for c in calls}),
    }


def fixture_stats(path: Path = FIXTURE) -> dict[str, Any]:
    if not path.exists():
        return {}
    records = read_fixture(path)
    tries = Counter(r["meta"]["tries"] for r in records)
    return {
        "records": len(records),
        "checked": sum(r["meta"]["checked"] for r in records),
        "first_try": sum(1 for r in records if r["meta"]["checked"] and r["meta"]["tries"] == 1),
        "tries": dict(sorted(tries.items())),
        "cold": sum(r["meta"]["is_cold"] and not r["meta"]["dup_of"] for r in records),
        "negative": sum(r["meta"]["is_negative"] for r in records),
        "dups": sum(1 for r in records if r["meta"]["dup_of"]),
        "english": sum(r["lang"] == "en" for r in records),
        "unchecked_ids": [r["id"] for r in records if not r["meta"]["checked"]],
    }


def seed_twice(run_id: int) -> dict[str, Any]:
    from polaris.cli import seed_records

    first = seed_records(FIXTURE, run_id)
    second = seed_records(FIXTURE, run_id)
    with session_scope() as s:
        in_db = s.execute(select(func.count()).select_from(RawItem)
                          .where(RawItem.is_synthetic.is_(True))).scalar_one()
    return {"first_inserted": first.inserted, "second_inserted": second.inserted,
            "fixture_items": first.received, "synthetic_in_db": in_db}


def cache_probe(run_id: int) -> dict[str, Any]:
    llm = LLM()
    for q in CACHE_PROBES:  # 第一轮：没缓存就真调一次
        llm.chat([{"role": "user", "content": q}], purpose="m1_cache_probe", run_id=run_id, cache=True)
    hits = sum(llm.chat([{"role": "user", "content": q}], purpose="m1_cache_probe", run_id=run_id,
                        cache=True).cache_hit for q in CACHE_PROBES)
    return {"probes": len(CACHE_PROBES), "second_round_hits": hits}


def render(stats: dict[str, Any]) -> str:
    sy, fx, sd, ca = stats["synth"], stats["fixture"], stats["seed"], stats["cache"]
    s = stats["settings"]
    lines = [
        "# M1 指标：项目骨架",
        "",
        f"生成时间 {stats['generated_at']}，代码版本 `{stats['git_commit']}`。"
        f"对话模型 `{s['models']['chat']}`，向量模型 `{s['embed_model']}`（{s['embed_dim']} 维）。",
        "",
        "开发期用的是第三方 OpenAI 兼容服务上的模型，这里的数字只说明骨架工作正常；"
        "正式指标以 Nemotron 在 Token Factory 上跑出的为准。",
        "",
        "## 模型调用追踪",
        "",
        "| 指标 | 结果 | 目标 |",
        "|---|---|---|",
    ]
    if sy:
        lines += [
            "| 追踪完整率（有 token 和耗时记录的调用 ÷ 拿到回答的调用） | "
            f"{sy['trace_complete']}/{sy['calls_answered']}"
            f"（{pct(sy['trace_complete'], sy['calls_answered'])}） | 100% |",
            f"| 结构化输出校验通过率（每次调用） | {sy['json_ok']}/{sy['calls_answered']}"
            f"（{pct(sy['json_ok'], sy['calls_answered'])}） | 记录 |",
            f"| 请求失败后重试 | {sy['request_errors'] or '无'} | 记录 |",
            f"| 延迟中位数 / 第 95 百分位 | {sy['latency_p50_ms']} ms / {sy['latency_p95_ms']} ms | 记录 |",
            f"| token（输入 / 输出 / 其中思考） | {sy['prompt_tokens']} / {sy['completion_tokens']} / "
            f"{sy['reasoning_tokens']} | 记录 |",
            f"| 费用 | {('$' + sy['cost_usd']) if sy['cost_usd'] else '开发期模型单价未知，未计'} | 记录 |",
        ]
    else:
        lines.append("| 还没有合成数据生成记录 | — | — |")
    lines += [
        "",
        f"数据来自合成数据生成那次运行（run {sy.get('run_id', '—')}）的全部 {sy.get('calls_total', 0)} 次调用。",
        "",
        "## 合成数据",
        "",
        "| 指标 | 结果 |",
        "|---|---|",
    ]
    if fx:
        lines += [
            f"| 条数 | {fx['records']}（冷门 {fx['cold']}、无关反例 {fx['negative']}、转载重复 {fx['dups']}、"
            f"英文 {fx['english']}） |",
            f"| 事实核对通过（给的事实都在、没给的没被编出来） | {fx['checked']}/{fx['records']}"
            f"（{pct(fx['checked'], fx['records'])}） |",
            f"| 第一次生成就通过 | {fx['first_try']}/{fx['records']}（{pct(fx['first_try'], fx['records'])}） |",
            f"| 生成次数分布 | {fx['tries']} |",
        ]
        if fx["unchecked_ids"]:
            lines.append(f"| 没通过核对的条目（评测时排除） | {', '.join(fx['unchecked_ids'])} |")
    lines += [
        "",
        "## 入库和缓存",
        "",
        "| 指标 | 结果 | 目标 |",
        "|---|---|---|",
        f"| 入库幂等：连续入库两次，第二次新增 | {sd['second_inserted']} 条 | 0 |",
        f"| 数据库里的合成原始条目 | {sd['synthetic_in_db']} 条（文件里 {sd['fixture_items']} 条） | 相等 |",
        f"| 缓存：同样的请求第二次命中 | {ca['second_round_hits']}/{ca['probes']} | 全部命中 |",
        "",
        "复现：`uv run polaris synth`（会调模型）、`uv run polaris seed`、`uv run polaris metrics m1`。",
        "",
    ]
    return "\n".join(lines)


def run() -> dict[str, Any]:
    with run_scope("eval", {"module": "M1"}) as (run_id, stats):
        stats.update({
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "git_commit": git_commit(),
            # 报告要进仓库：只留模型名，不留服务域名
            "settings": {k: v for k, v in get_settings().public_summary().items() if not k.endswith("_host")},
            "synth": synth_stats(),
            "fixture": fixture_stats(),
            "seed": seed_twice(run_id),
            "cache": cache_probe(run_id),
        })
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "M1.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2, default=str),
                                            encoding="utf-8", newline="\n")
        (REPORT_DIR / "M1.md").write_text(render(stats), encoding="utf-8", newline="\n")
    return stats
