"""求职领域的词表：技能和技术栈、面试题话题。市场统计（eval/market.py）、知识图谱（graph/）、检索的问题理解（search/）共用这一份。

JD_TERMS、QUESTION_TOPICS 是 10-11 统计 352 个 Agent 岗位和 947 道面试题时定的（eval/reports/market.md），改了会影响那份报告的数字。
SKILLS 在 JD_TERMS 上补了几个面试里常考、岗位里也常写的点，作为图谱里的技能节点。
"""
import re
from functools import lru_cache

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

SKILLS = JD_TERMS | {
    "Transformer": r"transformer|注意力机制|self.?attention", "KV Cache": r"kv.?cache", "多模态": r"多模态|multimodal|vlm|mllm",
    "CUDA/算子": r"cuda|算子|triton", "重排/Rerank": r"重排|rerank", "向量检索/Embedding": r"embedding|向量检索|向量召回",
    "分布式训练": r"分布式训练|megatron|deepspeed|fsdp", "搜索推荐": r"搜索|推荐系统|搜推|召回排序",
}


@lru_cache
def _compiled() -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple((name, re.compile(pat, re.I)) for name, pat in SKILLS.items())


def skills_in(text: str | None) -> list[str]:
    """一段文字里提到了哪些技能（按 SKILLS 的顺序，不重复）。"""
    if not text:
        return []
    return [name for name, pat in _compiled() if pat.search(text)]
