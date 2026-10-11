"""主动决策评测：`eval/gold/proactive_*.json` 里的场景，规则（proactive/decide.py）的去向对标准答案。不调模型。

指标参照 PersonaMem-v3 对"主动"和"克制"的区分：
- 推送精确率：推了的里面该推的比例；1 减它就是打扰率（不该推的推了）
- 推送召回率：该推的里面推了的比例；1 减它就是漏报率
- 三个去向（推 / 简报 / 不提）的整体准确率和混淆矩阵；"该不提却推了"单独数，那是最打扰的
"""
import json
from collections import Counter
from datetime import date, datetime
from typing import Any

from polaris.config import ROOT
from polaris.proactive.decide import Item, Profile, Sent, decide, explain

GOLD = ROOT / "eval" / "gold" / "proactive_v1.json"
HOLDOUT = ROOT / "eval" / "gold" / "proactive_holdout_v1.json"
REPORT = ROOT / "eval" / "reports" / "proactive.md"
CHANNELS = ("push", "brief", "drop")


def _dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


def _item(d: dict[str, Any]) -> Item:
    return Item(key=d["key"], kind=d["kind"], title=d["title"], company=d.get("company"), cities=d.get("cities", []),
                job_type=d.get("job_type"), directions=d.get("directions", []), text=d.get("text", ""),
                days_per_week=d.get("days_per_week"), duration_months=d.get("duration_months"),
                deadline=date.fromisoformat(d["deadline"]) if d.get("deadline") else None, first_seen=_dt(d.get("first_seen")),
                post_type=d.get("post_type"), has_referral=d.get("has_referral", False), simhash=d.get("simhash"))


def run(path=GOLD) -> dict[str, Any]:
    gold = json.loads(path.read_text(encoding="utf-8"))
    profile = Profile(**gold["profile"])
    rows = []
    for sc in gold["scenarios"]:
        history = [Sent(key=h["key"], company=h.get("company"), directions=h.get("directions", []), sent_at=_dt(h["sent_at"]),
                        feedback=h.get("feedback"), simhash=h.get("simhash")) for h in sc["history"]]
        decisions = {d.key: d for d in decide([_item(i) for i in sc["items"]], profile, history, _dt(sc["now"]), sc["level"])}
        for i in sc["items"]:
            d = decisions[i["key"]]
            rows.append({"scenario": sc["id"], "key": i["key"], "title": i["title"], "gold": i["gold"], "why": i["why"],
                         **explain(d)})
    return summarize(rows, gold["version"])


def summarize(rows: list[dict[str, Any]], version: str) -> dict[str, Any]:
    confusion = Counter((r["gold"], r["channel"]) for r in rows)
    pushed = [r for r in rows if r["channel"] == "push"]
    should = [r for r in rows if r["gold"] == "push"]
    hit = [r for r in pushed if r["gold"] == "push"]
    return {"suite": version, "items": len(rows), "accuracy": sum(r["gold"] == r["channel"] for r in rows) / len(rows),
            "push_precision": len(hit) / len(pushed) if pushed else None,
            "push_recall": len(hit) / len(should) if should else None,
            "pushed_should_drop": sum(1 for r in pushed if r["gold"] == "drop"),
            "confusion": {f"{g}->{c}": confusion[(g, c)] for g in CHANNELS for c in CHANNELS},
            "rows": rows}


def render(r: dict[str, Any], name: str) -> list[str]:
    from polaris.eval.m3 import fmt, pct

    ok = sum(x["gold"] == x["channel"] for x in r["rows"])
    lines = [f"### {name}：{r['suite']}（{r['items']} 条候选）", "",
             "| 指标 | 结果 |", "|---|---|",
             f"| 去向准确率（推 / 简报 / 不提） | {ok}/{r['items']}（{pct(ok, r['items'])}） |",
             f"| 推送精确率（1 − 打扰率） | {fmt(r['push_precision'])} |",
             f"| 推送召回率（1 − 漏报率） | {fmt(r['push_recall'])} |",
             f"| 该不提却推了 | {r['pushed_should_drop']} 条 |", "",
             "| 标准答案 → 实际 | 推 | 简报 | 不提 |", "|---|---|---|---|"]
    for g in CHANNELS:
        lines.append(f"| {g} | " + " | ".join(str(r["confusion"][f"{g}->{c}"]) for c in CHANNELS) + " |")
    wrong = [x for x in r["rows"] if x["gold"] != x["channel"]]
    if wrong:
        lines += ["", "不一致的：", "", "| 场景 | 候选 | 标准答案 | 实际 | 规则给的理由 |", "|---|---|---|---|---|"]
        for x in wrong:
            lines.append(f"| {x['scenario']} | {x['title']} | {x['gold']}（{x['why']}） | {x['channel']} | {'；'.join(x['reasons'])} |")
    return lines + [""]


def write_report() -> str:
    from datetime import datetime as dt

    dev, holdout = run(GOLD), run(HOLDOUT)
    lines = ["# 主动决策指标（M6）", "",
             f"生成于 {dt.now().astimezone().isoformat(timespec='seconds')}，命令 `polaris metrics proactive`（不调模型）。", "",
             "规则在 `src/polaris/proactive/decide.py`：匹配度、新颖度、紧迫度三个分数，去向是推 / 简报 / 不提，每条都写理由。"
             "开发集第一次跑 23 条错 4 条（杭州的岗位、算法岗进了简报，没在面的公司的面经、上海的招聘帖被推了），"
             "据此加了两条（城市、方向不沾边直接不提；面经只在正在面这家时推）；改完又暴露 2 条（到岗天数、时长对不上的岗位被推了，"
             "第一次是因为推送名额碰巧用完才进了简报），再加一条（条件对不上只进简报）。之后才写留出集。", "",
             "注意：规则和两份标准答案都是开发 agent 写的；留出集虽然在调完规则之后才写、写完才跑，仍可能不自觉地按规则出题，"
             "满分不能说明规则在真实使用里不会打扰人。需要作者本人按自己的习惯再标一批场景（见 `docs/STATUS.md`）。", ""]
    lines += render(dev, "开发集") + render(holdout, "留出集")
    text = "\n".join(lines)
    REPORT.write_text(text, encoding="utf-8", newline="\n")
    return text
