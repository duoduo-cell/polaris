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

REPORT = ROOT / "eval" / "reports" / "market.md"
AGENT_ROLE = re.compile(r"agent|智能体|大模型|llm|aigc|ai ?应用", re.I)
JD_TERMS = {
    "Python": r"python", "评测/Benchmark": r"评测|评估|benchmark|eval", "Prompt 工程": r"prompt|提示词",
    "规划/Planning": r"规划|planning|react\b|reflexion", "工具调用": r"function.?call|tool.?(use|call)|工具调用|函数调用",
    "RAG/检索增强": r"\brag\b|检索增强", "记忆/Memory": r"记忆|memory", "多 Agent": r"多.?agent|multi.?agent|多智能体",
    "上下文工程": r"上下文工程|context engineering|上下文管理", "Harness/沙箱": r"harness|沙箱|sandbox",
    "Coding Agent": r"coding.?agent|代码智能体|代码生成", "MCP": r"\bmcp\b|model context protocol",
    "LangChain": r"langchain", "LangGraph": r"langgraph", "AutoGen/CrewAI": r"autogen|crewai",
    "可观测": r"可观测|observab|链路追踪|tracing", "知识图谱": r"知识图谱|knowledge graph",
    "微调/SFT": r"微调|sft|fine.?tun|lora", "强化学习": r"强化学习|\brl\b|rlhf|grpo|ppo|dpo",
    "推理部署": r"vllm|sglang|推理加速|推理优化|tensorrt", "C++": r"c\+\+", "Go": r"\bgo\b|golang", "Java": r"\bjava\b",
    "TypeScript/前端": r"typescript|react|前端|javascript", "分布式/高并发": r"分布式|高并发|微服务",
    "Docker/K8s": r"docker|k8s|kubernetes|容器", "Redis": r"redis", "Kafka/消息队列": r"kafka|消息队列|rocketmq|rabbitmq",
    "向量数据库": r"向量数据库|milvus|faiss|pgvector|qdrant|weaviate",
}
QUESTION_TOPICS = {
    "项目深挖/落地": r"项目|落地|业务|难点|亮点", "RAG/检索": r"rag|检索|召回|向量|embedding|重排|rerank|切片|chunk",
    "Transformer/八股": r"transformer|注意力|attention|rope|kv.?cache|moe|lora|微调|sft",
    "工具调用/MCP": r"工具|tool|function|mcp|调用", "评测/指标": r"评测|评估|指标|benchmark|eval|准确率|成功率",
    "Agent 架构/循环": r"agent.?loop|agentloop|react|架构|范式|plan|规划|workflow",
    "工程/后端": r"并发|redis|mysql|kafka|缓存|分布式|数据库|稳定性|高可用", "记忆/上下文": r"记忆|memory|上下文|context|压缩",
    "强化学习": r"强化|ppo|dpo|grpo|rlhf|reward", "多 Agent": r"多.?agent|multi.?agent|子.?agent|subagent|协作",
    "手撕代码": r"手撕|算法题|leetcode|力扣|代码题", "LangChain/LangGraph": r"langchain|langgraph",
    "安全/幻觉": r"幻觉|安全|注入|权限|兜底|容错",
}


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
