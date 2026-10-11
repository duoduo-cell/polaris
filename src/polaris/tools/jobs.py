"""jobs_* / posts_*：查抽取好的结构化岗位（postings）和小红书笔记（posts）。

比 raw_items_* 多了按字段筛：公司（大厂的各种叫法都归到同一家）、城市、类型、方向；笔记按类型、能否内推筛。
M4 做多来源合并和混合检索后，jobs_* 改查合并后的 jobs 表，参数不变。
"""
import re
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import and_, any_, or_, select

from polaris.companies import match
from polaris.db.models import Post, Posting, RawItem
from polaris.db.session import session_scope
from polaris.tools.base import Page, ToolError, ToolSpec

JOB_TYPES = {"intern": "实习", "campus": "校招", "social": "社招"}


def _parse(value: str, prefix: str, tool: str, example: str) -> int:
    m = re.fullmatch(rf"\s*(?:{prefix}-)?(\d+)\s*", value, re.I)
    if not m:
        raise ToolError(f"编号「{value}」格式不对", f"用 {tool} 结果里的编号，形如 {prefix}-12", example)
    return int(m.group(1))


def _words_filter(column, query: str | None):
    return and_(*[column.ilike(f"%{w}%") for w in (query or "").split()])


def _company_filter(name_col, big_col, company: str, is_array: bool = False):
    big = match(company)
    if big:
        return any_(big_col) == big.name if is_array else big_col == big.name
    return any_(name_col) == company if is_array else name_col.ilike(f"%{company}%")


class JobsSearchArgs(Page):
    query: str | None = Field(None, max_length=100,
                              description="在岗位名和职位描述里找的词，空格分开、全部包含才算命中，如：Agent 后端")
    company: str | None = Field(None, description="公司，大厂的简称、英文名、产品名都行，如 字节、TikTok、鹅厂请写腾讯")
    city: str | None = Field(None, description="城市，如 北京；多城市岗位只要包含就算")
    job_type: Literal["intern", "campus", "social"] | None = Field(None, description="intern 实习、campus 校招、social 社招")
    direction: str | None = Field(None, description="方向，如 Agent、RAG、大模型、后端、算法")
    big_only: bool = Field(True, description="只看风向标大厂名单里的公司")


def _job_row(p: Posting, r: RawItem, detailed: bool) -> dict[str, Any]:
    out = {"id": f"J-{p.id}", "company": p.big_company or p.company_name, "title": p.title, "city": p.city,
           "type": JOB_TYPES.get(p.job_type or "", p.job_type)}
    for k in ("days_per_week", "duration_months", "salary_text", "can_convert", "deadline", "posted_at"):
        v = getattr(p, k)
        if v is not None:
            out[k] = v.isoformat() if hasattr(v, "isoformat") else v
    if detailed:
        out.update({"source": r.source, "url": r.url, "directions": p.directions, "fetched_at": r.fetched_at.isoformat(),
                    "raw_id": f"R-{r.id}"})
    return out


def jobs_search(args: JobsSearchArgs) -> dict:
    with session_scope() as s:
        stmt = select(Posting, RawItem).join(RawItem, RawItem.id == Posting.raw_item_id).where(
            Posting.is_synthetic.is_(False))
        if args.query:
            stmt = stmt.where(or_(_words_filter(Posting.title, args.query), _words_filter(Posting.jd_text, args.query)))
        if args.company:
            stmt = stmt.where(_company_filter(Posting.company_name, Posting.big_company, args.company))
        if args.city:
            stmt = stmt.where(Posting.city.ilike(f"%{args.city.removesuffix('市')}%"))
        if args.job_type:
            stmt = stmt.where(Posting.job_type == args.job_type)
        if args.direction:
            stmt = stmt.where(or_(any_(Posting.directions) == args.direction, Posting.title.ilike(f"%{args.direction}%")))
        if args.big_only:
            stmt = stmt.where(Posting.big_company.is_not(None))
        rows = s.execute(stmt.order_by(Posting.posted_at.desc().nulls_last(), Posting.id)
                         .limit(args.limit + 1).offset(args.offset)).all()
        items = [_job_row(p, r, args.response_format == "detailed") for p, r in rows[: args.limit]]
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
    pid = _parse(args.job_id, "J", "jobs_search", '{"job_id": "J-12"}')
    with session_scope() as s:
        row = s.execute(select(Posting, RawItem).join(RawItem, RawItem.id == Posting.raw_item_id)
                        .where(Posting.id == pid)).first()
        if row is None:
            raise ToolError(f"没有编号为 J-{pid} 的岗位", "先用 jobs_search 找到编号再取详情", '{"job_id": "J-12"}')
        p, r = row
        out = _job_row(p, r, True)
        out["requirements"] = p.requirements
        if args.response_format == "detailed":
            out["jd_text"] = (p.jd_text or "")[:3000]
    return out


class PostsSearchArgs(Page):
    query: str | None = Field(None, max_length=100, description="在笔记正文和概括里找的词，空格分开，如：面经 RAG")
    company: str | None = Field(None, description="笔记涉及的公司，如 字节、滴滴、米哈游")
    post_type: Literal["内推", "招聘", "面经", "经验", "求助", "其他"] | None = Field(None, description="笔记类型")
    has_referral: bool | None = Field(None, description="true 只看明确能内推的")


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
    with session_scope() as s:
        stmt = select(Post, RawItem).join(RawItem, RawItem.id == Post.raw_item_id)
        if args.query:
            stmt = stmt.where(or_(_words_filter(RawItem.raw_text, args.query), _words_filter(Post.summary, args.query)))
        if args.company:
            stmt = stmt.where(_company_filter(Post.companies, Post.big_companies, args.company, is_array=True))
        if args.post_type:
            stmt = stmt.where(Post.post_type == args.post_type)
        if args.has_referral is not None:
            stmt = stmt.where(Post.has_referral.is_(args.has_referral))
        rows = s.execute(stmt.order_by(Post.published_at.desc().nulls_last(), Post.id)
                         .limit(args.limit + 1).offset(args.offset)).all()
        items = [_post_row(p, r, args.response_format == "detailed") for p, r in rows[: args.limit]]
    has_more = len(rows) > args.limit
    result: dict = {"returned": len(items), "items": items, "next_offset": args.offset + args.limit if has_more else None}
    if not items:
        result["hint"] = "没有符合条件的笔记。可以去掉类型或公司条件、换个说法；确实没有就如实告诉用户。"
    return result


class PostsGetArgs(BaseModel):
    post_id: str = Field(..., description="笔记编号，如 P-3，来自 posts_search 的结果")
    response_format: Literal["concise", "detailed"] = Field("concise", description="detailed 再给笔记原文")


def posts_get(args: PostsGetArgs) -> dict:
    pid = _parse(args.post_id, "P", "posts_search", '{"post_id": "P-3"}')
    with session_scope() as s:
        row = s.execute(select(Post, RawItem).join(RawItem, RawItem.id == Post.raw_item_id).where(Post.id == pid)).first()
        if row is None:
            raise ToolError(f"没有编号为 P-{pid} 的笔记", "先用 posts_search 找到编号再取详情", '{"post_id": "P-3"}')
        p, r = row
        out = _post_row(p, r, True)
        out.update({"interview_questions": p.interview_questions, "advice": p.advice, "grad_year": p.grad_year})
        if args.response_format == "detailed":
            out["text"] = r.raw_text[:2500]
    return out


TOOLS = [
    ToolSpec(name="jobs_search",
             description="按条件查结构化岗位（公司、城市、类型、方向、关键词），默认只看大厂。返回岗位编号和关键字段；"
                         "要看要求和职位描述用 jobs_get。",
             args_schema=JobsSearchArgs, func=jobs_search, example='{"company": "字节", "city": "杭州", "query": "Agent"}'),
    ToolSpec(name="jobs_get", description="按编号取一个岗位的全部字段和任职要求；detailed 再给职位描述全文。",
             args_schema=JobsGetArgs, func=jobs_get, example='{"job_id": "J-12"}'),
    ToolSpec(name="posts_search",
             description="查小红书笔记抽出来的信息：面经、内推、招聘、经验帖。可按公司、类型、能否内推筛。",
             args_schema=PostsSearchArgs, func=posts_search, example='{"company": "滴滴", "post_type": "面经"}'),
    ToolSpec(name="posts_get", description="按编号取一条笔记的面试题、建议等；detailed 再给原文。",
             args_schema=PostsGetArgs, func=posts_get, example='{"post_id": "P-3"}'),
]
