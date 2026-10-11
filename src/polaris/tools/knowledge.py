"""questions_* / kg_*：面试题检索和知识图谱统计（M4）。

- questions_search：面经里拆出来的每道题单独成块（search/chunking.py），按相关度找题，出处是题目所在的帖子 P-。
- kg_stats：在 Neo4j 知识图谱上做"按什么分组、数一数"的统计，比如哪些公司的岗位要求 LangGraph、腾讯哪些部门在招 Agent、
  字节的面试题常考哪些技能。只开放几种固定的查询模式（不让模型写 Cypher），参数先校验再拼进模板。
"""
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from polaris.companies import match as big_company
from polaris.db.models import InterviewQuestion, Post, RawItem, SearchChunk
from polaris.db.session import session_scope
from polaris.interviews import MATERIAL_LABELS
from polaris.search.retrieve import SearchQuery, search
from polaris.sources import source_dict
from polaris.tools.base import ToolError, ToolSpec
from polaris.vocab import SKILLS, skills_in

DIRECTIONS = ("大模型", "Agent", "RAG", "评测", "多模态", "后端", "数据", "算法", "推理加速", "推荐搜索", "前端", "安全")


class QuestionsSearchArgs(BaseModel):
    query: str = Field(..., max_length=100, description="想找什么样的面试题，如：RAG 召回效果差怎么办、KV Cache、Agent 记忆怎么设计")
    company: str | None = Field(None, description="实际面试公司；评论提及和备考汇总不算，如 字节、腾讯")
    role: str | None = Field(None, max_length=100, description="岗位方向，如 后端、Agent")
    job_type: Literal["intern", "campus", "social"] | None = None
    round: str | None = Field(None, max_length=50, description="轮次，如 1面、2面")
    material: Literal["firsthand", "study", "uncertain"] | None = Field("firsthand", description="默认只看面经记录；备考填 study")
    category: str | None = Field(None, max_length=32)
    source: str | None = Field(None, max_length=64)
    since_days: int | None = Field(None, ge=1, le=365, description="只看最近多少天发的面经里的题")
    limit: int = Field(10, ge=1, le=30)


def questions_search(args: QuestionsSearchArgs) -> dict:
    since = datetime.now(UTC) - timedelta(days=args.since_days) if args.since_days else None
    hits, _ = search(SearchQuery(args.query, scope="questions", company=args.company, since=since, top_k=args.limit,
                                role=args.role, job_type=args.job_type, round=args.round, material=args.material,
                                category=args.category, source=args.source))
    with session_scope() as s:
        rows = {r.id: r for r in s.execute(select(SearchChunk).where(SearchChunk.id.in_([h.chunk_id for h in hits])))
                .scalars()}
        details = {q.key: (q, p, raw) for q, p, raw in s.execute(
            select(InterviewQuestion, Post, RawItem).join(Post, Post.id == InterviewQuestion.post_id)
            .join(RawItem, RawItem.id == Post.raw_item_id).where(InterviewQuestion.key.in_([h.key for h in hits])))}
        items = []
        for h in hits:
            r = rows.get(h.chunk_id)
            if r is None:
                continue
            item: dict[str, Any] = {"question": r.body, "from": f"P-{r.post_id}", "companies": r.companies}
            if r.key in details:
                q, p, raw = details[r.key]
                item.update(question_row(q, p, raw))
            if r.published_at:
                item["published"] = r.published_at.date().isoformat()
            items.append(item)
    out: dict[str, Any] = {"returned": len(items), "items": items}
    if not items:
        out["hint"] = "没找到相关的面试题。可以换个说法、去掉公司或时间条件；确实没有就如实告诉用户。"
    return out


def question_row(q: InterviewQuestion, p: Post, raw: RawItem) -> dict[str, Any]:
    return {"id": f"Q-{q.id}", "from": f"P-{p.id}", "display_title": q.question, "question": q.question,
            "companies": [q.company] if q.company else [], "company": q.company, "role": q.role, "team": q.team,
            "job_type": q.job_type, "round": q.round, "category": q.category, "material": q.material,
            "material_label": MATERIAL_LABELS[q.material], "attribution": q.attribution, "evidence": q.evidence,
            "interview_date": q.interview_date.isoformat() if q.interview_date else None,
            "published": q.published_at.date().isoformat() if q.published_at else None,
            "sources": [source_dict(raw.source, raw.url, raw_id=raw.id, fetched_at=raw.fetched_at, published_at=p.published_at)]}


class KgStatsArgs(BaseModel):
    of: Literal["jobs", "questions", "posts"] = Field("jobs", description="数什么：jobs 岗位、questions 面试题、posts 帖子")
    group_by: Literal["company", "skill", "unit", "city", "direction"] = Field(
        ..., description="按什么分组：company 公司、skill 技能、unit 部门（只对岗位和帖子）、city 城市（只对岗位）、direction 方向")
    company: str | None = Field(None, description="只看这家公司，如 腾讯、字节")
    skill: str | None = Field(None, description="只看涉及这个技能的，如 LangGraph、RAG、KV Cache、MCP、多 Agent")
    direction: str | None = Field(None, description=f"只看这个方向（岗位和帖子），可选：{'、'.join(DIRECTIONS)}")
    city: str | None = Field(None, description="只看这个城市的岗位，如 北京")
    top: int = Field(10, ge=1, le=30, description="返回前几组")


# 每种文档：可用的筛选和分组各走哪条关系
EDGES: dict[str, dict[str, tuple[str, str]]] = {
    "jobs": {"company": ("AT", "Company"), "skill": ("REQUIRES", "Skill"), "unit": ("IN", "Unit"),
             "city": ("LOCATED", "City"), "direction": ("DIRECTION", "Direction")},
    "questions": {"company": ("ASKED_AT", "Company"), "skill": ("TESTS", "Skill")},
    "posts": {"company": ("MENTIONS", "Company"), "skill": ("ABOUT", "Skill"), "unit": ("ABOUT_UNIT", "Unit"),
              "direction": ("ABOUT", "Direction")},
}
DOC_LABEL = {"jobs": "Job", "questions": "Question", "posts": "Post"}


def canonical_skill(text: str) -> str:
    low = text.strip().lower()
    for name in SKILLS:
        if low == name.lower() or low in [p.lower() for p in name.split("/")]:
            return name
    found = skills_in(text)
    if found:
        return found[0]
    raise ToolError(f"不认识技能「{text}」", f"换成词表里的叫法，比如：{'、'.join(list(SKILLS)[:15])}……", '{"skill": "RAG"}')


def kg_stats(args: KgStatsArgs) -> dict:
    from polaris.graph.client import driver, enabled

    edges = EDGES[args.of]  # 先校验参数：图谱关着时，参数错了也能得到怎么改
    filters = {"company": args.company, "skill": args.skill, "direction": args.direction, "city": args.city}
    for name, value in filters.items():
        if value and name not in edges:
            raise ToolError(f"{args.of} 不能按 {name} 筛", f"{args.of} 能用的条件：{'、'.join(edges)}", '{"of": "jobs"}')
    if args.group_by not in edges:
        raise ToolError(f"{args.of} 不能按 {args.group_by} 分组", f"{args.of} 能用的分组：{'、'.join(edges)}",
                        '{"of": "jobs", "group_by": "company"}')
    skill = canonical_skill(args.skill) if args.skill else None
    if not enabled():
        raise ToolError("知识图谱没有启用", "改用 jobs_search / posts_search 按条件查", '{"query": "..."}')
    params: dict[str, Any] = {"top": args.top}
    match = [f"(d:{DOC_LABEL[args.of]})"]
    if args.company:
        big = big_company(args.company)
        params["company"] = big.name if big else args.company
    if args.skill:
        params["skill"] = skill
    if args.direction:
        params["direction"] = args.direction
    if args.city:
        params["city"] = args.city.removesuffix("市")
    for name in ("company", "skill", "direction", "city"):
        if name in params:
            rel, label = edges[name]
            match.append(f"(d)-[:{rel}]->(:{label} {{name: ${name}}})")
    rel, label = edges[args.group_by]
    key = "g.label" if label == "Unit" else "g.name"
    ref = "d.id" if args.of != "questions" else "d.key"
    cypher = (f"MATCH {', '.join(match)} MATCH (d)-[:{rel}]->(g:{label}) "
              f"WITH {key} AS group, {ref} AS ref ORDER BY ref DESC "  # 例子取编号最大的（最新入库的），每次一样
              "WITH group, collect(DISTINCT ref) AS refs "
              "RETURN group, size(refs) AS n, refs[0..3] AS examples ORDER BY n DESC, group LIMIT $top")
    total_cypher = f"MATCH {', '.join(match)} RETURN count(DISTINCT d) AS n"
    d = driver()
    rows = d.execute_query(cypher, **params).records
    total = d.execute_query(total_cypher, **params).records[0]["n"]

    def cite(r: Any) -> str:
        if args.of == "jobs":
            return f"J-{r}"
        if args.of == "posts":
            return f"P-{r}"
        return f"P-{str(r).split(':')[1]}"  # 题目的出处是它所在的帖子

    items = [{"group": r["group"], "count": r["n"],
              "examples": list(dict.fromkeys(cite(x) for x in r["examples"]))} for r in rows]
    out: dict[str, Any] = {"of": args.of, "matched": total, "filters": {k: v for k, v in params.items() if k != "top"},
                           "groups": items}
    if not items:
        out["hint"] = "图谱里没有符合条件的。可以去掉一个条件再查；确实没有就如实告诉用户。"
    return out


TOOLS = [
    ToolSpec(name="questions_search",
             description="按相关度找面经里的面试题（每道题单独一条），可按公司、时间筛；出处是题目所在的帖子编号 P-。"
                         "问\"某公司面试问过什么\"\"某个知识点常怎么考\"用它。",
             args_schema=QuestionsSearchArgs, func=questions_search, example='{"query": "RAG 召回", "company": "字节"}'),
    ToolSpec(name="kg_stats",
             description="在知识图谱上分组计数：哪些公司的岗位要求某技能、某公司哪些部门在招、某公司面试常考哪些技能、"
                         "某方向的岗位在哪些城市。返回每组的数量和几个例子编号（J- 岗位，P- 帖子）。",
             args_schema=KgStatsArgs, func=kg_stats, example='{"of": "jobs", "group_by": "company", "skill": "LangGraph"}'),
]
