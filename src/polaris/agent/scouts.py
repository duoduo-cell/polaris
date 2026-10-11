"""侦察子 agent：每个来源一个，用 LangGraph 的 Send 并行跑。

每个侦察只读外部来源：跑一轮采集（原文直接存库）→ 把新条目抽成结构化信息 → 只把编号和简短汇报交回，
不碰知识库和记忆（那些只有主 agent 能写，M4 起）。小红书侦察还会读刚采到的笔记，提出新的查询词，
校验后加进轮换（origin=discovered），下一轮就会搜到——这是"越采越会搜"的部分。
依据：Anthropic 多 agent 研究系统（子 agent 并行、只交回精简结果）[A1]；LangGraph Send API [LG4]。
"""
import operator
import re
from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field
from sqlalchemy import select

from polaris.collectors import queries as q
from polaris.collectors.queries import directions_config
from polaris.collectors.rounds import collect_round
from polaris.companies import is_big
from polaris.config import get_settings
from polaris.db.models import Post, RawItem
from polaris.db.session import session_scope
from polaris.extract import run_extract
from polaris.llm import LLM

DISCOVER_PROMPT_VERSION = "discover-queries-v1"
MAX_DISCOVERED = 3


class ScoutState(TypedDict, total=False):
    sources: list[str]
    params: dict[str, dict[str, Any]]
    reports: Annotated[list[dict[str, Any]], operator.add]


class ScoutTask(TypedDict):
    source: str
    params: dict[str, Any]


# ---------------- 各来源的一轮 ----------------

def _tencent(p: dict[str, Any]) -> list[dict[str, Any]]:
    from polaris.collectors.tencent import TencentCollector
    return [collect_round("tencent", TencentCollector(), p.get("queries", 2), {"max_pages": p.get("max_pages", 2)})]


def _sites(p: dict[str, Any]) -> list[dict[str, Any]]:
    from polaris.collectors.sites import ByteDanceCollector, XhsCareersCollector
    # 两个都开浏览器，放在同一个侦察里先后跑
    return [collect_round("bytedance", ByteDanceCollector(), p.get("queries", 1), {"max_pages": p.get("max_pages", 2)}),
            collect_round("xhs_careers", XhsCareersCollector(), p.get("queries", 1), {"max_pages": p.get("max_pages", 2)})]


def _xhs(p: dict[str, Any]) -> list[dict[str, Any]]:
    from polaris.collectors.xhs import XhsCollector
    max_calls = p.get("max_calls", 20)
    return [collect_round("xhs", XhsCollector(max_calls=max_calls), p.get("queries", 3), {"max_calls": max_calls})]


def _boss(p: dict[str, Any]) -> list[dict[str, Any]]:
    from polaris.browser import attached
    from polaris.collectors.boss_web import BossWebCollector
    max_calls = p.get("max_calls", 30)
    with attached() as context:  # 在采集专用的 Edge 里浏览，见 browser.py
        return [collect_round("boss", BossWebCollector(context, max_calls=max_calls), p.get("queries", 2),
                              {"max_calls": max_calls, "via": "browser"})]


ROUNDS: dict[str, Callable[[dict[str, Any]], list[dict[str, Any]]]] = {
    "tencent": _tencent, "sites": _sites, "xhs": _xhs, "boss": _boss}
RAW_SOURCES = {"tencent": ["tencent:careers"], "sites": ["bytedance:campus", "xhs:careers"], "xhs": ["xhs"],
               "boss": ["boss"]}


# ---------------- 发现新查询词 ----------------

class DiscoveredQueries(BaseModel):
    queries: list[str] = Field(default_factory=list, description=f"新的搜索词，最多 {MAX_DISCOVERED} 个")
    reason: str = Field("", description="一句话说明为什么值得搜")


DISCOVER_SYSTEM = """你在帮一个求职 agent 扩展小红书搜索词。目标：找风向标大厂的大模型、Agent 方向的实习、内推和面经。
给你刚采到的笔记（标题、涉及公司、团队、岗位）和已有的搜索词。提出最多 3 个新搜索词：
- 每个 2 到 20 个字，像真人在小红书搜的说法，比如"字节 Seed Agent 实习"、"美团 大模型 日常实习"；
- 要么带具体大厂或部门名（字节、腾讯、阿里、美团、小红书……或 Seed、混元、通义这类团队），要么带具体方向；
- 和已有搜索词意思不能重复；只根据给的笔记里真实出现的公司、团队、岗位来提，不要编。
没有值得加的就返回空列表。"""


def valid_query(text: str, known: set[str]) -> bool:
    """新查询词的硬性校验：长度、要带大厂或方向词、不和已有的重复（忽略空格和大小写）。"""
    t = text.strip()
    norm = re.sub(r"\s+", "", t).lower()
    if not 2 <= len(t) <= 20 or norm in {re.sub(r"\s+", "", k).lower() for k in known}:
        return False
    directions = directions_config()["directions"] + ["实习", "内推", "面经", "校招"]
    return is_big(t) or any(d.lower() in t.lower() for d in directions)


def discover_queries(new_ids: list[int], run_id: int | None = None, llm: LLM | None = None) -> list[str]:
    if not new_ids:
        return []
    with session_scope() as s:
        rows = s.execute(select(RawItem.raw_text, Post.big_companies, Post.team, Post.roles)
                         .join(Post, Post.raw_item_id == RawItem.id, isouter=True)
                         .where(RawItem.id.in_(new_ids))).all()
    notes = [f"- {text.splitlines()[0][:60]}｜公司：{'、'.join(big or [])}｜团队：{team or ''}｜岗位：{'、'.join(roles or [])}"
             for text, big, team, roles in rows]
    known = q.existing("xhs")
    prompt = "刚采到的笔记：\n" + "\n".join(notes[:30]) + "\n\n已有搜索词（部分）：\n" + "、".join(sorted(known)[:120])
    r = (llm or LLM()).extract(prompt, DiscoveredQueries, purpose="discover_queries", role="extract",
                               system=DISCOVER_SYSTEM, run_id=run_id, prompt_version=DISCOVER_PROMPT_VERSION)
    if not r.ok or r.value is None:
        return []
    picked = [x.strip() for x in r.value.queries if valid_query(x, known)][:MAX_DISCOVERED]
    return q.add_discovered("xhs", picked)


# ---------------- 图 ----------------

def fan_out(state: ScoutState) -> list[Send]:
    return [Send("scout", {"source": s, "params": (state.get("params") or {}).get(s, {})}) for s in state["sources"]]


def scout(task: ScoutTask) -> dict[str, Any]:
    source = task["source"]
    try:
        rounds = ROUNDS[source](task["params"])
        new_ids = [i for r in rounds for i in r["new_ids"]]
        run_id = rounds[0]["run_id"] if rounds else None
        kind = "post" if source == "xhs" else "job"
        extracted = failed = 0
        for raw_source in RAW_SOURCES[source]:
            out = run_extract(kind, len(new_ids) + 10, run_id, get_settings().llm_concurrency, raw_source)
            extracted, failed = extracted + out["extracted"], failed + out["failed"]
        discovered = discover_queries(new_ids, run_id) if source == "xhs" else []
        report = {"source": source, "ok": True, "new": len(new_ids), "new_ids": [f"R-{i}" for i in new_ids[:50]],
                  "extracted": extracted, "extract_failed": failed,
                  "big_company_items": sum(r.get("big_company_items", 0) for r in rounds),
                  "failures": sum(r.get("failures", 0) for r in rounds),
                  "rate_limited": sum(r.get("rate_limited", 0) for r in rounds),
                  "stopped": next((r.get("stopped") for r in rounds if r.get("stopped")), None),
                  "searched_queries": [x for r in rounds for x in r.get("searched_queries", [])],
                  "discovered_queries": discovered}
    except Exception as e:  # noqa: BLE001  一个来源出问题不影响别的来源
        report = {"source": source, "ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    return {"reports": [report]}


def build_graph():
    g = StateGraph(ScoutState)
    g.add_node("scout", scout)
    g.add_conditional_edges(START, fan_out, ["scout"])
    g.add_edge("scout", END)
    return g.compile()


def run_scouts(sources: list[str], params: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    unknown = set(sources) - set(ROUNDS)
    if unknown:
        raise ValueError(f"没有这些来源的侦察：{', '.join(sorted(unknown))}；可选 {', '.join(ROUNDS)}")
    state = build_graph().invoke({"sources": sources, "params": params or {}, "reports": []})
    return sorted(state["reports"], key=lambda r: sources.index(r["source"]))
