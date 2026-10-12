"""可观测性汇总：最近几天和你对话的每次回答用了多久、调了多少次模型和工具、多少 token、工具出错分几类、停止和失败多少。
给网页的"追踪"页用；数据都来自已经记下的表（chat_messages 里每次回答的 trace、tool_calls、llm_calls、chat_tasks），不另外埋点。
"""
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from polaris.db.models import ChatMessage, ChatTask, LLMCall, ToolCall
from polaris.db.session import session_scope


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def overview(days: int = 7, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    with session_scope() as s:
        answers = s.scalars(select(ChatMessage).where(ChatMessage.role == "assistant", ChatMessage.created_at >= since)
                            .order_by(ChatMessage.id.desc())).all()
        tasks = Counter(s.scalars(select(ChatTask.state).where(ChatTask.created_at >= since)).all())
        tools = s.execute(select(ToolCall.tool, ToolCall.ok, ToolCall.error_kind, ToolCall.latency_ms)
                          .where(ToolCall.agent == "main", ToolCall.created_at >= since)).all()
        memory_ms = list(s.scalars(select(LLMCall.latency_ms).where(LLMCall.purpose == "memory_extract",
                                                                     LLMCall.created_at >= since)).all())
        rows = []
        for m in answers:
            turn = m.turn or {}
            totals = (turn.get("trace") or {}).get("totals") or {}
            rows.append({"id": m.id, "thread_id": m.thread_id, "at": m.created_at.isoformat(timespec="seconds"),
                         "question": (turn.get("question") or "")[:60], "seconds": round((turn.get("latency_ms") or 0) / 1000, 1),
                         "model_calls": totals.get("model_calls"),
                         "tool_calls": totals.get("tool_calls", len(turn.get("tool_calls") or [])),
                         "tool_errors": totals.get("tool_errors"), "input_tokens": totals.get("input_tokens"),
                         "cached_tokens": totals.get("cached_tokens"), "output_tokens": totals.get("output_tokens"),
                         "compacted": bool(turn.get("compacted")), "live": sum(1 for t in (turn.get("trace") or {}).get("tools", [])
                                                                              if t.get("live"))})
    secs = [r["seconds"] for r in rows if r["seconds"]]
    traced = [r for r in rows if r["model_calls"] is not None]
    by_tool: dict[str, dict[str, Any]] = {}
    for tool, ok, kind, ms in tools:
        d = by_tool.setdefault(tool, {"tool": tool, "calls": 0, "errors": 0, "kinds": Counter(), "ms": []})
        d["calls"] += 1
        d["ms"].append(ms)
        if not ok:
            d["errors"] += 1
            d["kinds"][kind or "unknown"] += 1
    tool_rows = sorted(({"tool": d["tool"], "calls": d["calls"], "errors": d["errors"], "p50_ms": _pct(d["ms"], 0.5),
                         "error_kinds": dict(d["kinds"])} for d in by_tool.values()), key=lambda x: -x["calls"])
    kinds = Counter()
    for d in by_tool.values():
        kinds.update(d["kinds"])
    input_tokens = sum(r["input_tokens"] or 0 for r in traced)
    return {
        "days": days, "answers": len(rows),
        "latency": {"p50": _pct(secs, 0.5), "p90": _pct(secs, 0.9), "max": max(secs) if secs else None},
        "per_answer": {"model_calls": round(sum(r["model_calls"] for r in traced) / len(traced), 1) if traced else None,
                       "tool_calls": round(sum(r["tool_calls"] or 0 for r in rows) / len(rows), 1) if rows else None,
                       "input_tokens": round(input_tokens / len(traced)) if traced else None,
                       "output_tokens": round(sum(r["output_tokens"] or 0 for r in traced) / len(traced)) if traced else None},
        "cache_hit": round(sum(r["cached_tokens"] or 0 for r in traced) / input_tokens, 3) if input_tokens else None,
        "tools": {"calls": len(tools), "errors": sum(not ok for _, ok, _, _ in tools), "error_kinds": dict(kinds), "by_tool": tool_rows},
        "tasks": dict(tasks), "compacted": sum(r["compacted"] for r in rows), "live_lookups": sum(r["live"] for r in rows),
        "memory_extract_p50_ms": _pct([float(x) for x in memory_ms if x], 0.5),
        "recent": rows[:40],
    }
