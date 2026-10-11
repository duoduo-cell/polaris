"""最小 agent：用来验证"agent → 工具 → 追踪 → 回放"整条链。

三套配置：minimal 只有 raw_items_* 两个工具（M2，合成数据）；search 查结构化的岗位和笔记（M3，真实数据）；
knowledge 再加面试题检索和知识图谱统计（M4）。

用 LangChain 1.x 的 create_agent（构建在 LangGraph 上）；重试和调用上限用官方中间件。
"""
import time
from dataclasses import dataclass, field
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware
from langchain_core.language_models.chat_models import BaseChatModel

from polaris.agent.models import chat_model, replay_model
from polaris.agent.tracing import PolarisTracer
from polaris.llm.recorder import DbRecorder, MemoryRecorder
from polaris.replay import Cassette
from polaris.tools import REGISTRY
from polaris.tools.runtime import FaultKind, ToolRuntime

PROMPT_VERSION = "minimal-v1"
SYSTEM_PROMPT = """你是北极星的检索助手，帮用户在采集到的岗位原文里找信息。
- 只根据工具返回的内容回答；工具里找不到就直接说找不到，不要编。
- 回答时写上依据的原始条目编号（如 R-12）。
- 和找岗位无关的问题，直接简短回答，不要调用工具。
- 工具报错时，按它给的"怎么改"调整后再试。"""
TOOL_NAMES = ("raw_items_search", "raw_items_get")

SEARCH_PROMPT = """你是北极星的检索助手，帮用户在采集到的大厂岗位和小红书笔记里找信息。
- 岗位用 jobs_search / jobs_get，面经、内推、招聘帖用 posts_search / posts_get；只根据工具返回的内容回答。
- 用户说的公司可能是简称、英文名或黑话（字节、TikTok、鹅厂、pdd），直接传给工具，工具会归一。
- 找不到就直接说没找到，并说明查了哪些条件，不要编。
- 回答时写上依据的编号（岗位如 J-12，笔记如 P-3）。
- 和找岗位无关的问题，直接简短回答，不要调用工具。
- 工具报错时，按它给的"怎么改"调整后再试。"""


KNOWLEDGE_PROMPT = """你是北极星，帮求职者在采集到的大厂岗位、帖子（小红书、脉脉）、面经题目和知识图谱里找信息、做比较。
工具怎么选：
- 找岗位：jobs_search（可按公司、城市、类型、方向、最近多少天筛；query 写想找什么，按相关度排）；看要求、各处来源、说法冲突用 jobs_get。
- 找帖子（面经、内推、招聘、经验）：posts_search / posts_get。
- 找面试题（"某公司问过什么""某个知识点怎么考"）：questions_search。
- 统计和分布（"哪些公司要求某技能""某公司哪些部门在招""面试常考哪些""哪个城市多"）：kg_stats，再用 jobs_get / posts_get 看例子。
回答规则：
- 只根据工具返回的内容回答，每个结论写上依据的编号（岗位如 J-12，帖子如 P-3）。
- 比较类问题两边都查到再下结论，写出两边的数字；数量是"库里采到的"，不是全网。
- jobs_get 返回 conflicts（各处说法不一致）时，说清取了哪个值、另一种说法是什么、来自哪里。
- 推荐岗位时只推荐工具结果里确实符合用户条件（城市、类型、方向）的，并写出理由。
- 找不到就直接说没找到，并说明查了哪些条件，不要编；放宽两三次条件还是没有，就停下来如实回答，不要一直换条件查。
- 用户说的公司可能是简称、英文名或黑话（字节、TikTok、鹅厂、pdd），直接传给工具。
- 和找岗位无关的问题，直接简短回答，不要调用工具。
- 工具报错时，按它给的"怎么改"调整后再试。"""


@dataclass(frozen=True)
class Profile:
    name: str
    prompt_version: str
    system_prompt: str
    tools: tuple[str, ...]
    max_model_calls: int = 8


MINIMAL = Profile("minimal", PROMPT_VERSION, SYSTEM_PROMPT, TOOL_NAMES)
SEARCH = Profile("search", "search-v1", SEARCH_PROMPT, ("jobs_search", "jobs_get", "posts_search", "posts_get"))
KNOWLEDGE = Profile("knowledge", "knowledge-v2", KNOWLEDGE_PROMPT,
                    ("jobs_search", "jobs_get", "posts_search", "posts_get", "questions_search", "kg_stats"),
                    max_model_calls=12)  # 比较、统计类题要多查几次


@dataclass
class AgentRun:
    question: str
    answer: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    llm_call_ids: list[int | None] = field(default_factory=list)
    latency_ms: int = 0
    error: str | None = None


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content or [])


def build_minimal_agent(model: BaseChatModel, tools: list[Any], system_prompt: str = SYSTEM_PROMPT, max_model_calls: int = 8):
    return create_agent(
        model=model, tools=tools, system_prompt=system_prompt,
        middleware=[ModelRetryMiddleware(max_retries=3),
                    ModelCallLimitMiddleware(run_limit=max_model_calls, exit_behavior="end")])


def ask(question: str, *, run_id: int | None = None, thread_id: str | None = None,
        cassette: Cassette | None = None, faults: dict[str, list[FaultKind]] | None = None,
        purpose: str = "agent_minimal", model: BaseChatModel | None = None, record_db: bool = True,
        profile: Profile = MINIMAL) -> AgentRun:
    runtime = ToolRuntime(run_id=run_id, thread_id=thread_id, agent=profile.name, cassette=cassette,
                          faults=faults, record_db=record_db)
    tools = runtime.langchain_tools([REGISTRY[n] for n in profile.tools])
    replaying = cassette is not None and cassette.llm_replay
    llm = model or (replay_model(cassette) if replaying else chat_model("chat"))
    tracer = PolarisTracer(purpose=purpose + (":replay" if replaying else ""), run_id=run_id,
                           prompt_version=profile.prompt_version, cassette=cassette,
                           recorder=DbRecorder() if record_db else MemoryRecorder())
    agent = build_minimal_agent(llm, tools, profile.system_prompt, profile.max_model_calls)
    start = time.perf_counter()
    try:
        state = agent.invoke({"messages": [{"role": "user", "content": question}]},
                             # 步数上限要大于"模型调用上限 × 2"：先到调用上限时能正常收尾，而不是撞上步数上限报错
                             config={"callbacks": [tracer], "recursion_limit": 2 * profile.max_model_calls + 5})
        answer, error = text_of(state["messages"][-1].content), None
    except Exception as e:  # noqa: BLE001  评测要记下失败，而不是整批中断
        answer, error = "", f"{type(e).__name__}: {e}"
    return AgentRun(question=question, answer=answer, tool_calls=runtime.calls, llm_call_ids=tracer.call_ids,
                    latency_ms=int((time.perf_counter() - start) * 1000), error=error)
