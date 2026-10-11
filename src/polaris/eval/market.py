"""岗位市场分析：从采到的大厂 Agent / 大模型岗位描述里数技术要求，从面经里数面试题的主题。

用来回答"这个项目该展示什么"：计划和技术选型按这里的数字排优先级，不凭印象。
结果写到 eval/reports/market.md（只有汇总数字；面经原文是 B 档，不出本机）。
"""
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from polaris.config import ROOT
from polaris.db.models import Post, Posting, RawItem
from polaris.db.session import session_scope
from polaris.vocab import JD_TERMS, QUESTION_TOPICS

REPORT = ROOT / "eval" / "reports" / "market.md"
AGENT_ROLE = re.compile(r"agent|智能体|大模型|llm|aigc|ai ?应用", re.I)



def jd_stats() -> dict[str, Any]:
    with session_scope() as s:
        rows = s.execute(select(Posting.title, Posting.jd_text, Posting.job_type).join(RawItem, RawItem.id == Posting.raw_item_id)
                         .where(Posting.is_synthetic.is_(False), Posting.big_company.is_not(None))).all()
    agent = [r for r in rows if AGENT_ROLE.search((r.title or "") + (r.jd_text or "")[:400])]
    junior = [r for r in agent if r.job_type in ("intern", "campus")]

    def count(rs: list[Any]) -> Counter:
        c: Counter = Counter()
        for r in rs:
            text = r.jd_text or ""
            c.update(name for name, pat in JD_TERMS.items() if re.search(pat, text, re.I))
        return c

    return {"total": len(rows), "agent": len(agent), "junior": len(junior), "all": count(agent), "junior_terms": count(junior)}


def question_stats() -> dict[str, Any]:
    with session_scope() as s:
        rows = s.execute(select(RawItem.source, Post.interview_questions).join(RawItem, RawItem.id == Post.raw_item_id)
                         .where(Post.post_type == "面经")).all()
    qs = [q for _, qs_ in rows for q in (qs_ or [])]
    c: Counter = Counter()
    for q in qs:
        c.update(name for name, pat in QUESTION_TOPICS.items() if re.search(pat, q, re.I))
    return {"posts": len(rows), "by_source": dict(Counter(src for src, _ in rows)), "questions": len(qs), "topics": c}


def render(jd: dict[str, Any], qs: dict[str, Any]) -> str:
    n, j = max(1, jd["agent"]), max(1, jd["junior"])
    lines = ["# 大厂 Agent 岗位要什么、面试问什么", "",
             f"生成时间 {datetime.now(UTC):%Y-%m-%d %H:%M} UTC。数据：北极星采到的风向标大厂岗位 {jd['total']} 个，"
             f"其中 Agent / 大模型相关 {jd['agent']} 个（实习和校招 {jd['junior']} 个）；"
             f"面经 {qs['posts']} 条、面试题 {qs['questions']} 道。"
             "按岗位描述里是否出现关键词计数，一个岗位出现多次只算一次。", "",
             "## 岗位描述里的技术要求", "", "| 技术 | 全部相关岗位 | 实习和校招 |", "|---|---|---|"]
    for name, k in jd["all"].most_common():
        jk = jd["junior_terms"][name]
        lines.append(f"| {name} | {k}（{k * 100 // n}%） | {jk}（{jk * 100 // j}%） |")
    lines += ["", "## 面试题的主题", "", "| 主题 | 题数 |", "|---|---|"]
    q = max(1, qs["questions"])
    lines += [f"| {name} | {k}（{k * 100 // q}%） |" for name, k in qs["topics"].most_common()]
    lines += ["", "复现：`uv run polaris metrics market`。", ""]
    return "\n".join(lines)


def run() -> str:
    text = render(jd_stats(), question_stats())
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text, encoding="utf-8")
    return text
