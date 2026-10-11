"""真实问答探针：按 eval/gold/probe_v*.json 用真实模型跑一遍，逐轮存下回答、步骤、卡片、耗时、记忆变化和压缩事件，给人逐条看。

不是打分基准，是找问题用的（10-12 用户要求：先看回答有没有深度、有没有出处、是不是空话）。
只许连单独的库：拒绝真实库 polaris 和你正在用的沙盒。对话原文会带帖子内容，存 data/probe/，不进仓库。

    $env:DATABASE_URL = "postgresql+psycopg://polaris:polaris@127.0.0.1:5432/polaris_live"
    uv run python -m polaris.eval.probe eval/gold/probe_v1.json
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from polaris.config import ROOT, get_settings

FORBIDDEN = ("polaris", "polaris_sandbox")


def _turn(question: str, thread_id: str) -> dict[str, Any]:
    from polaris.agent import main

    events: list[dict[str, Any]] = []
    start = time.monotonic()

    def on(e: dict[str, Any]) -> None:
        events.append({**e, "t": round(time.monotonic() - start, 1)})

    turn = main.chat(question, thread_id, on_event=on)
    drafts = [e for e in events if e["type"] == "draft"]
    return {
        "question": question, "answer": turn.answer, "error": turn.error, "latency_s": round(turn.latency_ms / 1000, 1),
        "first_tool_s": next((e["t"] for e in events if e["type"] == "tool"), None),
        "first_draft_s": drafts[0]["t"] if drafts else None,
        "steps": [{"desc": a.get("desc"), "summary": a.get("summary"), "ok": a.get("ok"), "args": a.get("args")} for a in turn.activities],
        "results": [{"id": r.get("id"), "kind": r.get("kind"), "title": r.get("display_title"),
                     "sources": [s.get("url") for s in r.get("sources", [])][:3], "match": (r.get("match") or {}).get("score")}
                    for r in turn.results],
        "memory": [{k: e.get(k) for k in ("op", "label", "value", "old_value", "qualifier")} for e in turn.memory_events
                   if e.get("op") not in ("noop", "skip", "suppressed")],
        "compacted": turn.compacted, "context": turn.context,
    }


def _render(rows: list[dict[str, Any]], spec: dict[str, Any]) -> str:
    expect = {t["id"]: t for t in spec["threads"]}
    out = [f"# 真实问答探针 {datetime.now():%m-%d %H:%M}\n", f"库：{get_settings().database_url.rsplit('/', 1)[-1]}\n"]
    for row in rows:
        meta = expect.get(row["id"], {})
        out.append(f"\n## {row['id']} {meta.get('kind', '')}\n")
        if meta.get("expect"):
            out.append(f"> 好回答应有：{meta['expect']}\n")
        for i, t in enumerate(row["turns"], 1):
            out.append(f"\n### {row['id']}.{i} {t['question']}\n")
            out.append(f"- 用时 {t['latency_s']} 秒（第一步 {t['first_tool_s']} 秒，开始写回答 {t['first_draft_s']} 秒）；"
                       f"上下文 {(t['context'] or {}).get('tokens')}；错误：{t['error']}")
            for s in t["steps"]:
                out.append(f"- 步骤：{s['desc']} → {s['summary']}")
            if t["compacted"]:
                out.append(f"- 压缩：{t['compacted']['messages']} 条，{t['compacted']['before_tokens']} → {t['compacted']['after_tokens']}")
            for m in t["memory"]:
                out.append(f"- 记忆：{m}")
            for r in t["results"]:
                out.append(f"- 卡片：{r['id']} {r['title']}（匹配 {r['match']}）{' '.join(u for u in r['sources'] if u)}")
            out.append("\n" + (t["answer"] or "（没有回答）") + "\n")
    return "\n".join(out)


def run(path: Path, workers: int = 4, only: list[str] | None = None) -> Path:
    db = get_settings().database_url.rsplit("/", 1)[-1]
    if db in FORBIDDEN:
        raise SystemExit(f"不在 {db} 上跑探针：会写进聊天记录和记忆。先把 DATABASE_URL 指向单独的库（如 polaris_live）。")
    spec = json.loads(path.read_text(encoding="utf-8"))
    out = ROOT / "data" / "probe" / datetime.now().strftime("%m%d-%H%M")
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    persona = {"id": "P", "turns": [_turn(q, f"probe-{stamp}-persona") for q in spec.get("persona", [])]}
    threads = [t for t in spec["threads"] if not only or t["id"] in only]

    def one(t: dict[str, Any]) -> dict[str, Any]:
        row = {"id": t["id"], "turns": []}
        for q in t["turns"]:
            row["turns"].append(_turn(q, f"probe-{stamp}-{t['id']}"))
            (out / f"{t['id']}.json").write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
        return row

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = [persona, *pool.map(one, threads)]
    from polaris.tools.memory import MemoryCardArgs, memory_card

    (out / "memory_card.json").write_text(json.dumps(memory_card(MemoryCardArgs()), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "all.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "report.md").write_text(_render(rows, spec), encoding="utf-8")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", type=Path)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    print(run(a.spec, a.workers, a.only))
