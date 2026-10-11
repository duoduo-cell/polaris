"""工具调用评测套件 v0：题目从合成数据的标准答案自动生成，按最终结果评分（不评路径）。

参照：τ-bench 比最终状态、算 pass^k；BFCL 的"无关请求不调工具"；故障注入参照 ToolMisuseBench。
"""
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select

from polaris.db.models import RawItem
from polaris.db.session import session_scope
from polaris.synth.jobs import FIXTURE, read_fixture

SUITE_VERSION = "tool-calling-v0"


@dataclass
class Task:
    id: str
    category: str  # lookup / english / conflict / irrelevant / abstain / recovery
    question: str
    must_contain: list[list[str]] = field(default_factory=list)  # 每组至少命中一个
    cite: list[str] = field(default_factory=list)  # 回答里要写出的条目编号（任一即可）
    expect_tools: bool = True
    faults: dict[str, list[str]] = field(default_factory=dict)
    cite_min: int = 1  # 至少写出几个 cite 里的编号（推荐类题要多个）
    cite_only: str | None = None  # 给前缀（如 "J-"）时，回答里这类编号必须都在 cite 里：推荐了不符合条件的岗位算错


def date_forms(d: date) -> list[str]:
    return [f"{d.year}年{d.month}月{d.day}日", f"{d.month}月{d.day}日", d.isoformat(), f"{d.month}/{d.day}",
            f"{d.year}.{d.month}.{d.day}", f"{d.month}.{d.day}"]


def build_tasks() -> list[Task]:
    records = read_fixture(FIXTURE)
    with session_scope() as s:
        ids = dict(s.execute(select(RawItem.external_id, RawItem.id).where(RawItem.is_synthetic.is_(True))).all())
    by_id = {r["id"]: r for r in records}
    in_dup = {r["meta"]["dup_of"] for r in records if r["meta"]["dup_of"]} | {
        r["id"] for r in records if r["meta"]["dup_of"]}
    pairs = [(r["truth"]["company"], r["truth"]["title"]) for r in records]
    single = [r for r in records if r["id"] not in in_dup and pairs.count((r["truth"]["company"], r["truth"]["title"])) == 1
              and r["id"] in ids]

    def rid(r: dict[str, Any]) -> str:
        return f"R-{ids[r['id']]}"

    tasks: list[Task] = []
    salary = [r for r in single if r["lang"] == "zh" and r["truth"]["salary_min"]][:4]
    for i, r in enumerate(salary):
        t = r["truth"]
        tasks.append(Task(f"salary-{i + 1}", "lookup", f"{t['company']}的「{t['title']}」日薪是多少？请写出依据的原始条目编号。",
                          [[str(int(t["salary_min"]))], [str(int(t["salary_max"]))]], [rid(r)]))
    deadline = [r for r in single if r["lang"] == "zh" and r["truth"]["deadline"] and r not in salary][:3]
    for i, r in enumerate(deadline):
        t = r["truth"]
        tasks.append(Task(f"deadline-{i + 1}", "lookup", f"{t['company']}的「{t['title']}」投递截止是哪天？请写出依据的原始条目编号。",
                          [date_forms(date.fromisoformat(t["deadline"]))], [rid(r)]))
    english = next((r for r in single if r["lang"] == "en" and r["truth"]["deadline"]), None)
    if english:
        t = english["truth"]
        d = date.fromisoformat(t["deadline"])
        tasks.append(Task("english-1", "english", f"What is the application deadline for {t['company']}'s "
                          f"\"{t['title']}\"? Cite the raw item id.",
                          [date_forms(d) + [f"{d.strftime('%B')} {d.day}", f"{d.strftime('%b')} {d.day}"]],
                          [rid(english)]))
    conflicts = [r for r in records if r["meta"]["dup_of"] and r["meta"]["conflict_field"] == "deadline"
                 and r["id"] in ids and r["meta"]["dup_of"] in ids][:2]
    for i, dup in enumerate(conflicts):
        base = by_id[dup["meta"]["dup_of"]]
        t = base["truth"]
        tasks.append(Task(f"conflict-{i + 1}", "conflict", f"{t['company']}的「{t['title']}」投递截止是哪天？请写出依据的原始条目编号。",
                          [date_forms(date.fromisoformat(t["deadline"])),
                           date_forms(date.fromisoformat(dup["truth"]["deadline"]))], [rid(base), rid(dup)]))
    tasks.append(Task("irrelevant-1", "irrelevant", "用一句话解释什么是 Transformer。", expect_tools=False))
    tasks.append(Task("abstain-1", "abstain", "采集到的原文里有没有「量子计算编译器实习生」这个岗位？",
                      [["没有", "找不到", "未找到", "没找到", "未检索到", "不存在"]]))
    for i, (tool, fault) in enumerate([("raw_items_search", "rate_limit"), ("raw_items_get", "timeout")]):
        base = salary[i]
        tasks.append(Task(f"recovery-{i + 1}", "recovery", base_question(base), tasks[i].must_contain, tasks[i].cite,
                          faults={tool: [fault]}))
    for t in tasks:
        t.must_contain = [[w for w in group if w] for group in t.must_contain]
    return tasks


def base_question(r: dict[str, Any]) -> str:
    t = r["truth"]
    return f"{t['company']}的「{t['title']}」日薪是多少？请写出依据的原始条目编号。"


def cited(short_id: str, answer: str) -> bool:
    """编号是否出现在回答里。不用单词边界：中文紧挨着编号时（如"编号J-12"）不算边界，会漏判。"""
    return re.search(rf"(?<![A-Za-z0-9-]){re.escape(short_id)}(?!\d)", answer) is not None


def check(task: Task, answer: str, tool_calls: list[dict[str, Any]], error: str | None) -> dict[str, Any]:
    """按最终结果打分：回答里有没有正确的值、有没有写出依据编号、该不该调工具。"""
    flat = re.sub(r"\s+", "", answer)
    used_tools = len(tool_calls) > 0
    values_ok = all(any(re.sub(r"\s+", "", w) in flat for w in group) for group in task.must_contain)
    cite_ok = not task.cite or any(cited(c, answer) for c in task.cite)
    tools_ok = used_tools if task.expect_tools else not used_tools
    if task.category == "conflict":  # 两个来源的说法都要指出来
        cite_ok = all(cited(c, answer) for c in task.cite)
    stray: list[str] = []
    if task.cite_min > 1 or task.cite_only:
        cite_ok = sum(cited(c, answer) for c in task.cite) >= task.cite_min
        if task.cite_only:
            mentioned = set(re.findall(rf"(?<![A-Za-z0-9-]){re.escape(task.cite_only)}\d+", answer))
            stray = sorted(mentioned - set(task.cite))
            cite_ok = cite_ok and not stray
    success = error is None and values_ok and cite_ok and tools_ok
    return {"success": success, "values_ok": values_ok, "cite_ok": cite_ok, "tools_ok": tools_ok, "stray": stray,
            "n_tool_calls": len(tool_calls),
            "invalid_args": sum(1 for c in tool_calls if c["error_kind"] == "invalid_args"),
            "injected": sum(1 for c in tool_calls if c["error_kind"] == "injected")}
