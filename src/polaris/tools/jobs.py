"""jobs_* / posts_*：查合并后的岗位（jobs）和帖子（posts）。

- 按字段筛：公司（大厂的各种叫法都归到同一家）、城市、类型、方向；帖子按类型、能否内推筛。
- 有 query 时走混合检索（search/retrieve.py：关键词 + 向量 + 图谱，重排），按相关度排；没有 query 按发布时间排。
- 岗位是多来源对齐后的（align/）：一条岗位可能合并了多条挂牌，字段说法不一致的记在 conflicts 里，
  jobs_get 给出每条来源和裁决依据，回答时能说清"这个数是哪来的"。
M4 起编号 J- 指合并后的岗位（之前指单条挂牌），参数不变。
"""
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import any_, or_, select

from polaris.companies import match
from polaris.db.models import Job, Post, Posting, RawItem
from polaris.db.session import session_scope
from polaris.search.retrieve import SearchQuery, search
from polaris.tools.base import Page, ToolError, ToolSpec

JOB_TYPES = {"intern": "实习", "campus": "校招", "social": "社招"}
SEARCH_DEPTH = 60  # 混合检索取多少块，再按字段筛


def _parse(value: str, prefix: str, tool: str, example: str) -> int:
    m = re.fullmatch(rf"\s*(?:{prefix}-)?(\d+)\s*", value, re.I)
    if not m:
        raise ToolError(f"编号「{value}」格式不对", f"用 {tool} 结果里的编号，形如 {prefix}-12", example)
    return int(m.group(1))


def _company_filter(name_col, big_col, company: str, is_array: bool = False):
    big = match(company)
    if big:
        return any_(big_col) == big.name if is_array else big_col == big.name
    return any_(name_col) == company if is_array else name_col.ilike(f"%{company}%")


def ranked_ids(query: str, scope: str, company: str | None) -> list[int]:
    """混合检索，返回按相关度排好的岗位编号或帖子编号（同一文档只取第一次出现）。"""
    hits, _ = search(SearchQuery(query, scope=scope, company=company, top_k=SEARCH_DEPTH))
    ids = [h.job_id if scope == "jobs" else h.post_id for h in hits]
    return list(dict.fromkeys(i for i in ids if i))


def _in_order(rows: list[Any], order: list[int], key) -> list[Any]:
    pos = {i: n for n, i in enumerate(order)}
    return sorted(rows, key=lambda r: pos.get(key(r), len(pos)))


class JobsSearchArgs(Page):
    query: str | None = Field(None, max_length=100,
                              description="想找什么样的岗位，自然语言或关键词都行，按相关度排，如：要求 RAG 的 Agent 后端实习")
    company: str | None = Field(None, description="公司，大厂的简称、英文名、产品名都行，如 字节、TikTok、鹅厂请写腾讯")
    city: str | None = Field(None, description="城市，如 北京；多城市岗位只要包含就算")
    job_type: Literal["intern", "campus", "social"] | None = Field(None, description="intern 实习、campus 校招、social 社招")
    direction: str | None = Field(None, description="方向，如 Agent、RAG、大模型、后端、算法")
    big_only: bool = Field(True, description="只看风向标大厂名单里的公司")
    since_days: int | None = Field(None, ge=1, le=365, description="只看最近多少天发布的岗位（没写发布日期的岗位会被排除）")


def _job_row(j: Job, detailed: bool) -> dict[str, Any]:
    out = {"id": f"J-{j.id}", "company": j.company or j.company_name, "title": j.title, "city": j.city,
           "type": JOB_TYPES.get(j.job_type or "", j.job_type)}
    for k in ("days_per_week", "duration_months", "salary_text", "can_convert", "deadline", "posted_at"):
        v = getattr(j, k)
        if v is not None:
            out[k] = v.isoformat() if hasattr(v, "isoformat") else v
    if j.posting_count > 1:
        out["listings"] = j.posting_count  # 合并了几条挂牌（多城市、多来源）
    if j.conflicts:
        out["conflicting_fields"] = sorted({c["field"] for c in j.conflicts})  # 各处说法不一致的字段，详情用 jobs_get
    if detailed:
        out.update({"unit": j.unit, "directions": j.directions, "last_seen": j.last_seen_at.date().isoformat()})
    return out


def jobs_search(args: JobsSearchArgs) -> dict:
    order = ranked_ids(args.query, "jobs", args.company) if args.query else None
    with session_scope() as s:
        stmt = select(Job).where(Job.is_synthetic.is_(False), Job.status == "active")
        if order is not None:
            stmt = stmt.where(Job.id.in_(order))
        if args.company:
            stmt = stmt.where(_company_filter(Job.company_name, Job.company, args.company))
        if args.city:
            stmt = stmt.where(Job.city.ilike(f"%{args.city.removesuffix('市')}%"))
        if args.job_type:
            stmt = stmt.where(Job.job_type == args.job_type)
        if args.direction:
            stmt = stmt.where(or_(any_(Job.directions) == args.direction, Job.title.ilike(f"%{args.direction}%")))
        if args.big_only:
            stmt = stmt.where(Job.company_id.is_not(None))
        if args.since_days:
            stmt = stmt.where(Job.posted_at >= date.today() - timedelta(days=args.since_days))
        if order is None:
            rows = s.execute(stmt.order_by(Job.posted_at.desc().nulls_last(), Job.id)
                             .limit(args.limit + 1).offset(args.offset)).scalars().all()
        else:
            rows = _in_order(s.execute(stmt).scalars().all(), order, lambda j: j.id)
            rows = rows[args.offset: args.offset + args.limit + 1]
        items = [_job_row(j, args.response_format == "detailed") for j in rows[: args.limit]]
    has_more = len(rows) > args.limit
    result: dict = {"returned": len(items), "items": items, "next_offset": args.offset + args.limit if has_more else None}
    if not items:
        result["hint"] = ("没有符合条件的岗位。可以去掉一两个条件（比如城市、类型）、换个说法，"
                          "或者库里确实还没采到这家公司的岗位——那就如实告诉用户。")
    return result


class JobsGetArgs(BaseModel):
    job_id: str = Field(..., description="岗位编号，如 J-12，来自 jobs_search 的结果")
    response_format: Literal["concise", "detailed"] = Field("concise", description="detailed 再给职位描述全文")


def jobs_get(args: JobsGetArgs) -> dict:
    jid = _parse(args.job_id, "J", "jobs_search", '{"job_id": "J-12"}')
    with session_scope() as s:
        j = s.get(Job, jid)
        if j is None:
            raise ToolError(f"没有编号为 J-{jid} 的岗位", "先用 jobs_search 找到编号再取详情", '{"job_id": "J-12"}')
        out = _job_row(j, True)
        out["requirements"] = j.requirements
        listings = s.execute(select(RawItem.source, RawItem.url, RawItem.fetched_at, Posting.city)
                             .join(RawItem, RawItem.id == Posting.raw_item_id).where(Posting.job_id == j.id)
                             .order_by(Posting.id)).all()
        out["sources"] = [{"source": r.source, "url": r.url, "city": r.city, "fetched_at": r.fetched_at.date().isoformat()}
                          for r in listings]
        if j.conflicts:  # 说法不一致的字段：取了哪个、为什么、另外的说法来自哪里
            out["conflicts"] = [{"field": c["field"], "chosen": c["chosen"], "rule": c["rule"],
                                 "others": [{"value": a["value"], "sources": a["sources"]} for a in c["alternatives"]]}
                                for c in j.conflicts]
        if args.response_format == "detailed":
            out["jd_text"] = (j.jd_text or "")[:3000]
    return out


class PostsSearchArgs(Page):
    query: str | None = Field(None, max_length=100,
                              description="想找什么样的帖子，自然语言或关键词都行，按相关度排，如：字节 Agent 面经 问了 RAG")
    company: str | None = Field(None, description="帖子涉及的公司，如 字节、滴滴、米哈游")
    post_type: Literal["内推", "招聘", "面经", "经验", "求助", "其他"] | None = Field(None, description="帖子类型")
    has_referral: bool | None = Field(None, description="true 只看明确能内推的")
    since_days: int | None = Field(None, ge=1, le=365, description="只看最近多少天发的帖子")


def _post_row(p: Post, r: RawItem, detailed: bool) -> dict[str, Any]:
    out = {"id": f"P-{p.id}", "type": p.post_type, "companies": p.big_companies or p.companies, "summary": p.summary}
    for k in ("team", "city", "days_per_week", "duration_months", "can_convert", "interview_rounds"):
        v = getattr(p, k)
        if v is not None:
            out[k] = v
    if p.has_referral:
        out["has_referral"] = True
    if p.published_at:
        out["published"] = p.published_at.date().isoformat()
    if detailed:
        out.update({"roles": p.roles, "likes": p.likes, "url": r.url, "raw_id": f"R-{r.id}",
                    "interview_questions": p.interview_questions[:8]})
    return out


def posts_search(args: PostsSearchArgs) -> dict:
    order = ranked_ids(args.query, "posts", args.company) if args.query else None
    with session_scope() as s:
        stmt = select(Post, RawItem).join(RawItem, RawItem.id == Post.raw_item_id)
        if order is not None:
            stmt = stmt.where(Post.id.in_(order))
        if args.company:
            stmt = stmt.where(_company_filter(Post.companies, Post.big_companies, args.company, is_array=True))
        if args.post_type:
            stmt = stmt.where(Post.post_type == args.post_type)
        if args.has_referral is not None:
            stmt = stmt.where(Post.has_referral.is_(args.has_referral))
        if args.since_days:
            stmt = stmt.where(Post.published_at >= datetime.now(UTC) - timedelta(days=args.since_days))
        if order is None:
            rows = s.execute(stmt.order_by(Post.published_at.desc().nulls_last(), Post.id)
                             .limit(args.limit + 1).offset(args.offset)).all()
        else:
            rows = _in_order(s.execute(stmt).all(), order, lambda row: row[0].id)[args.offset: args.offset + args.limit + 1]
        items = [_post_row(p, r, args.response_format == "detailed") for p, r in rows[: args.limit]]
    has_more = len(rows) > args.limit
    result: dict = {"returned": len(items), "items": items, "next_offset": args.offset + args.limit if has_more else None}
    if not items:
        result["hint"] = "没有符合条件的帖子。可以去掉类型或公司条件、换个说法；确实没有就如实告诉用户。"
    return result


class PostsGetArgs(BaseModel):
    post_id: str = Field(..., description="帖子编号，如 P-3，来自 posts_search 的结果")
    response_format: Literal["concise", "detailed"] = Field("concise", description="detailed 再给帖子原文")


def posts_get(args: PostsGetArgs) -> dict:
    pid = _parse(args.post_id, "P", "posts_search", '{"post_id": "P-3"}')
    with session_scope() as s:
        row = s.execute(select(Post, RawItem).join(RawItem, RawItem.id == Post.raw_item_id).where(Post.id == pid)).first()
        if row is None:
            raise ToolError(f"没有编号为 P-{pid} 的帖子", "先用 posts_search 找到编号再取详情", '{"post_id": "P-3"}')
        p, r = row
        out = _post_row(p, r, True)
        out.update({"interview_questions": p.interview_questions, "advice": p.advice, "grad_year": p.grad_year})
        if args.response_format == "detailed":
            out["text"] = r.raw_text[:2500]
    return out


TOOLS = [
    ToolSpec(name="jobs_search",
             description="查岗位（多来源合并后的）：按公司、城市、类型、方向筛，给 query 时按相关度排，默认只看大厂。"
                         "返回岗位编号和关键字段；各处说法不一致的字段会标出来。要看要求、来源和冲突详情用 jobs_get。",
             args_schema=JobsSearchArgs, func=jobs_search, example='{"company": "字节", "city": "杭州", "query": "Agent"}'),
    ToolSpec(name="jobs_get", description="按编号取一个岗位的全部字段、任职要求、每条来源，以及说法不一致的字段取了哪个、为什么；"
                                          "detailed 再给职位描述全文。",
             args_schema=JobsGetArgs, func=jobs_get, example='{"job_id": "J-12"}'),
    ToolSpec(name="posts_search",
             description="查帖子（小红书、脉脉）抽出来的信息：面经、内推、招聘、经验帖。可按公司、类型、能否内推筛，"
                         "给 query 时按相关度排。",
             args_schema=PostsSearchArgs, func=posts_search, example='{"company": "滴滴", "post_type": "面经"}'),
    ToolSpec(name="posts_get", description="按编号取一条帖子的面试题、建议等；detailed 再给原文。",
             args_schema=PostsGetArgs, func=posts_get, example='{"post_id": "P-3"}'),
]
