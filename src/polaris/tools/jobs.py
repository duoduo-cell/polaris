"""jobs_* / posts_*：查合并后的岗位（jobs）和帖子（posts）。

- 按字段筛：公司（大厂的各种叫法都归到同一家）、城市、类型、方向；帖子按类型、能否内推筛。
- 有 query 时走混合检索（search/retrieve.py：关键词 + 向量 + 图谱，重排），按相关度排；没有 query 按发布时间排。
- 岗位是多来源对齐后的（align/）：一条岗位可能合并了多条挂牌，字段说法不一致的记在 conflicts 里，
  jobs_get 给出每条来源和裁决依据，回答时能说清"这个数是哪来的"。
M4 起编号 J- 指合并后的岗位（之前指单条挂牌），参数不变。
"""
import re
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import any_, func, or_, select

from polaris.companies import match
from polaris.db.models import InterviewQuestion, Job, Post, Posting, RawItem
from polaris.db.session import session_scope
from polaris.followups import states
from polaris.interviews import profile
from polaris.search.retrieve import SearchQuery, degraded, search
from polaris.sources import date_kind, source_dict
from polaris.tools.base import Page, ToolError, ToolSpec, untrusted

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


def ranked_ids(query: str, scope: str, company: str | None) -> tuple[list[int], str | None]:
    """混合检索，返回按相关度排好的岗位编号或帖子编号（同一文档只取第一次出现），以及检索打了折扣时的说明。"""
    hits, trace = search(SearchQuery(query, scope=scope, company=company, top_k=SEARCH_DEPTH))
    ids = [h.job_id if scope == "jobs" else h.post_id for h in hits]
    return list(dict.fromkeys(i for i in ids if i)), degraded(trace)


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
    since_days: int | None = Field(None, ge=1, le=365, description="最近N个自然日（含今天）；发布日期未知的排除，不用抓取日期凑数")
    date_basis: Literal["published", "reported"] = Field("published", description=(
        "published只用明确来源发布日期；reported允许来源更新时间，只在用户要求看最近更新时用"))
    max_days_per_week: int | None = Field(None, ge=1, le=7, description=(
        "每周最多能去几天：只留 JD 写明的到岗天数不超过它的岗位。用户说每周能去几天、问有没有只要去 N 天的，用这个筛，"
        "不要把「每周2天」当关键词搜。没写天数的岗位不算进来，结果里另报个数"))


def _job_row(j: Job, detailed: bool) -> dict[str, Any]:
    out = {"id": f"J-{j.id}", "company": j.company or j.company_name, "title": j.title, "city": j.city,
           "type": JOB_TYPES.get(j.job_type or "", j.job_type), "job_type": j.job_type, "unit": j.unit,
           "display_title": " · ".join(x for x in (j.company or j.company_name, j.title) if x),
           "last_seen": j.last_seen_at.date().isoformat() if j.last_seen_at else None}
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


def job_sources(s, ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    rows = s.execute(select(Posting.job_id, Posting.city, RawItem).join(RawItem, RawItem.id == Posting.raw_item_id)
                     .where(Posting.job_id.in_(ids)).order_by(Posting.id)).all()
    sources: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
    for jid, city, r in rows:
        source = source_dict(r.source, r.url, raw_id=r.id, city=city, fetched_at=r.fetched_at, published_at=r.published_at)
        source["date_kind"] = date_kind(r)
        if not any(v["url"] == source["url"] and v["source"] == source["source"] and v["city"] == city for v in sources[jid]):
            sources[jid].append(source)
    return sources


def job_date_kinds(s, ids: list[int]) -> dict[int, str]:
    rows = s.execute(select(Posting.job_id, Posting.posted_at, RawItem).join(RawItem, RawItem.id == Posting.raw_item_id)
                     .where(Posting.job_id.in_(ids))).all()
    jobs = {j.id: j for j in s.scalars(select(Job).where(Job.id.in_(ids)))}
    kinds: dict[int, set[str]] = {i: set() for i in ids}
    for jid, posted, raw in rows:
        job = jobs[jid]
        if job.posted_at and posted == job.posted_at:
            kinds[jid].add(date_kind(raw))
    return {jid: "published" if "published" in values else "updated" if "updated" in values else "unknown"
            for jid, values in kinds.items()}


def library_note(kind: str, dated: bool = False) -> dict[str, Any]:
    """告诉模型查的是什么：本地库里的采集副本，最近一次采到是什么时候（10-12 探针里模型把查本地库说成了"搜了小红书"）。"""
    from sqlalchemy import func

    from polaris.report import BEIJING

    with session_scope() as s:
        latest = s.scalar(select(func.max(RawItem.fetched_at)).where(RawItem.kind == kind))
        from polaris.sources import label

        counts = dict(s.execute(select(RawItem.source, func.count()).where(RawItem.kind == kind, ~RawItem.source.like("synthetic%"))
                                .group_by(RawItem.source)).all())
        have = "、".join(f"{label(src)} {n}" for src, n in sorted(counts.items(), key=lambda x: -x[1]))
        missing = [label(src) for src in (("boss",) if kind == "job" else ("xhs", "maimai", "nowcoder")) if src not in counts]
        out: dict[str, Any] = {"scope": "本地库：常驻采集存下的副本，不是刚刚去网站或平台现搜的",
                               "latest_fetch": latest.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M") if latest else None,
                               "by_source": "原始采集记录数（不同来源可能是同一岗位，不可相加当岗位数）：" + have
                               + (f"；{'、'.join(missing)}还没有采到" if missing else "")}
        if dated:
            total = s.scalar(select(func.count()).select_from(Job).where(Job.is_synthetic.is_(False), Job.status == "active"))
            known = s.scalar(select(func.count()).select_from(Job).where(Job.is_synthetic.is_(False), Job.status == "active",
                                                                          Job.posted_at.is_not(None)))
            out["dated"] = (f"在库岗位有来源日期的为 {known}/{total} 个（可能是发布或更新）；"
                            "仅明确发布日期能进入 published 筛选，具体排除数量见 scope")
    return out


class JobsTrendArgs(BaseModel):
    days: int = Field(14, ge=3, le=90, description="看最近多少天")
    group_by: Literal["company", "direction", "city"] = Field("company", description="按公司、方向还是城市分组")
    company: str | None = Field(None, description="只看这家公司")
    direction: str | None = Field(None, description="只看这个方向，如 Agent")
    job_type: Literal["intern", "campus", "social"] | None = Field(None, description="intern 实习、campus 校招、social 社招")


def jobs_trend(args: JobsTrendArgs) -> dict:
    """最近一段时间新出现、下架了哪些岗位。数据来自官网每天的整批重采（daemon.official_daily），快照不够时如实说。"""
    from collections import Counter

    from polaris.align.build import official_snapshots
    from polaris.db.models import Run

    now = datetime.now(UTC)
    since = now - timedelta(days=args.days)
    with session_scope() as s:
        stmt = select(Job).where(Job.is_synthetic.is_(False))
        if args.company:
            stmt = stmt.where(_company_filter(Job.company_name, Job.company, args.company))
        if args.direction:
            stmt = stmt.where(Job.directions.any(args.direction))
        if args.job_type:
            stmt = stmt.where(Job.job_type == args.job_type)
        jobs = s.scalars(stmt).all()
        runs = [t for p, t in s.execute(select(Run.params, Run.started_at).where(Run.kind == "collect", Run.status == "ok",
                                                                            Run.started_at >= since))
                if (p or {}).get("mode") == "official-daily"]
        first_ever = s.scalar(select(func.min(RawItem.first_seen_at)).where(RawItem.kind == "job",
                                                                           ~RawItem.source.like("synthetic%")))

    def keys(j: Job) -> list[str]:
        if args.group_by == "company":
            return [j.company or j.company_name or "未知"]
        return list((j.directions if args.group_by == "direction" else j.cities) or ["未写"])

    new = [j for j in jobs if j.first_seen_at and j.first_seen_at >= since]
    gone = [j for j in jobs if j.status == "gone" and j.last_seen_at and j.last_seen_at >= since]
    by_new, by_gone = Counter(k for j in new for k in keys(j)), Counter(k for j in gone for k in keys(j))
    days = sorted({t.astimezone(timezone(timedelta(hours=8))).date() for t in runs})
    out: dict = {"days": args.days, "active_now": sum(j.status == "active" for j in jobs), "new": len(new), "gone": len(gone),
                 "groups": [{"group": k, "new": by_new[k], "gone": by_gone[k]}
                            for k in sorted(set(by_new) | set(by_gone), key=lambda k: -(by_new[k] + by_gone[k]))[:15]],
                 "examples_new": [f"J-{j.id}" for j in sorted(new, key=lambda j: j.first_seen_at, reverse=True)[:5]],
                 "snapshot_days": len(days), "official_snapshots": list(official_snapshots())}
    notes = []
    if first_ever and first_ever >= since:
        notes.append(f"岗位采集从 {first_ever:%Y-%m-%d} 才开始，那之前就在招的岗位第一次被看到也算'新出现'，"
                     "这段时间的'新出现'不代表公司在加招")
    if len(days) < 7:
        notes.append(f"这段时间官网每天整批重采只有 {len(days)} 天，下架判断和趋势都还不可靠；至少攒一周再谈变化")
    if notes:
        out["caveats"] = notes
    return out


def jobs_search(args: JobsSearchArgs) -> dict:
    from polaris.matching_scope import criteria

    current = criteria(args.model_dump())
    # 先核实结构化范围，避免在已无候选时仍付费换关键词；不在数据库事务中等待检索服务。
    with session_scope() as s:
        stmt = select(Job).where(Job.is_synthetic.is_(False), Job.status == "active")
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
            stmt = stmt.where(Job.posted_at >= date.today() - timedelta(days=args.since_days - 1), Job.posted_at <= date.today())
        days = None
        if args.max_days_per_week:
            # 10-12 回答评测：没有这个筛选时，模型拿"每周2天"做全文检索，换了 16 种说法、调到上限也答不准
            days = {"max_days_per_week": args.max_days_per_week,
                    "jd_without_days": s.scalar(stmt.where(Job.days_per_week.is_(None)).with_only_columns(func.count(Job.id))),
                    "least_days_in_jd": s.scalar(stmt.with_only_columns(func.min(Job.days_per_week))),
                    "note": "关键词检索前的范围统计；只算 JD 写明天数的岗位，没写不能当成符合"}
            stmt = stmt.where(Job.days_per_week <= args.max_days_per_week)
        rows = s.scalars(stmt.order_by(Job.posted_at.desc().nulls_last(), Job.id)).all()
        kinds = job_date_kinds(s, [j.id for j in rows])
        excluded = sum(kinds[j.id] != "published" for j in rows) if args.since_days and args.date_basis == "published" else 0
        excluded_rows = [j for j in rows if kinds[j.id] != "published"][:3] if excluded else []
        if args.since_days and args.date_basis == "published":
            rows = [j for j in rows if kinds[j.id] == "published"]
    candidates = len(rows)
    order, note = ranked_ids(args.query, "jobs", args.company) if args.query and candidates else (None, None)
    if order is not None:
        ids = set(order)
        rows = _in_order([j for j in rows if j.id in ids], order, lambda j: j.id)
    retrieved = len(rows)
    with session_scope() as s:
        from polaris.match import matches

        example_fits = matches(s, [j.id for j in excluded_rows], criteria=current) if excluded_rows else {}
        example_sources = job_sources(s, [j.id for j in excluded_rows]) if excluded_rows else {}
        excluded_examples = [{"id": f"J-{j.id}", "title": j.title, "company": j.company or j.company_name,
                              "reported_date": j.posted_at.isoformat(), "date_kind": kinds[j.id],
                              "reason": "来源只给更新时间" if kinds[j.id] == "updated" else "无法核实是发布日期",
                              "match": example_fits.get(j.id), "sources": example_sources[j.id]} for j in excluded_rows]
        rows = rows[args.offset: args.offset + args.limit + 1]
        items = [_job_row(j, args.response_format == "detailed") for j in rows[: args.limit]]
        sources = job_sources(s, [j.id for j in rows[:args.limit]])
        progress = states(s, [j.id for j in rows[:args.limit]])
        from polaris.match import matches

        fits = matches(s, [j.id for j in rows[:args.limit]], criteria=current)
        for j, item in zip(rows[:args.limit], items, strict=True):
            item["sources"] = sources[j.id][:3]
            item["source_count"] = len(sources[j.id])
            item["followup"] = progress[j.id]
            item["posted_at_kind"] = kinds[j.id]
            if j.id in fits:
                item["match"] = fits[j.id]
    has_more = len(rows) > args.limit
    result: dict = {"pagination": {"candidate_count": retrieved,
                                  "count_basis": "关键词已召回的候选子集（不是所有相关岗位）" if args.query else "本地结构化范围全量候选",
                                  "scope_candidate_count": candidates},
                    "returned": len(items), "items": items, "offset": args.offset,
                    "next_offset": args.offset + args.limit if has_more else None,
                    "library": library_note("job", dated=bool(args.since_days))}
    result["scope"] = {"company": args.company, "city": args.city, "direction": args.direction, "job_type": args.job_type,
                       "since_days": args.since_days, "date_basis": args.date_basis,
                       "date_from": (date.today() - timedelta(days=args.since_days - 1)).isoformat() if args.since_days else None,
                       "date_to": date.today().isoformat(), "excluded_unconfirmed_publication_dates": excluded,
                       "note": "来源发布日期不保证是岗位首次上线；更新时间、首次采到、最后采到均不能冒充发布时间"}
    if days:
        result["days_filter"] = days
    if excluded_examples:
        result["excluded_examples"] = excluded_examples
    if note:
        result["search_note"] = note
    if not candidates:
        status, action = "scope_empty", "stop"
        hint = ("本地库在当前结构化条件及日期口径下没有候选，换关键词不能增加结果。保持用户条件并停止该范围的补查；"
                "说明查询范围、采集时效和排除原因。不能推断全平台没有岗位；放宽条件须由用户明确允许。")
    elif not retrieved:
        status, action = "query_empty", "retry_query"
        hint = ("结构化范围内有候选，但当前关键词检索未命中；这不证明没有符合需求的岗位。"
                "保持所有筛选条件，最多换一种等价说法，或不带关键词查看同范围候选再核对；不要扩大日期、城市或类型。")
    elif not items:
        status, action = "page_exhausted", "previous_page"
        hint = "当前页越过已检索候选末尾，不是没有岗位；复用前页结果或返回有效的offset，保持所有筛选条件。"
    else:
        status, action = "matches", "use_results"
        hint = None
    result["search"] = {"status": status, "action": action, "candidates_before_query": candidates,
                        "retrieved_candidates": retrieved, "query_executed": bool(args.query and candidates),
                        "note": "计数仅证明本地结构化范围，不证明方向语义或个人资格匹配；关键词召回最多取60块，不是全量"}
    if hint:
        result["hint"] = hint
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
        out["sources"] = job_sources(s, [j.id])[j.id]
        out["field_sources"] = j.field_sources
        out["status"] = j.status
        out["posted_at_kind"] = job_date_kinds(s, [j.id])[j.id]
        from polaris.match import matches

        if fit := matches(s, [j.id]).get(j.id):
            out["match"] = fit
        if j.conflicts:  # 说法不一致的字段：取了哪个、为什么、另外的说法来自哪里
            out["conflicts"] = [{"field": c["field"], "chosen": c["chosen"], "rule": c["rule"],
                                 "others": [{"value": a["value"], "sources": a["sources"]} for a in c["alternatives"]]}
                                for c in j.conflicts]
        if args.response_format == "detailed":
            out["jd_text"] = untrusted((j.jd_text or "")[:3000])
    return out


class PostsSearchArgs(Page):
    keyword: str | None = Field(None, max_length=100, description="按文字包含筛选，不进行语义检索")
    query: str | None = Field(None, max_length=100,
                              description="想找什么样的帖子，自然语言或关键词都行，按相关度排，如：字节 Agent 面经 问了 RAG")
    company: str | None = Field(None, description="帖子涉及的公司，如 字节、滴滴、米哈游")
    post_type: Literal["内推", "招聘", "面经", "经验", "求助", "爆料", "其他"] | None = Field(None, description="帖子类型")
    material: Literal["firsthand", "study", "uncertain"] | None = Field(None, description="面经记录、备考整理、待核实")
    role: str | None = Field(None, max_length=100, description="岗位方向，如 后端、Agent 开发")
    job_type: Literal["intern", "campus", "social"] | None = None
    source: str | None = Field(None, max_length=64, description="来源，如 xhs 小红书、maimai 脉脉")
    has_referral: bool | None = Field(None, description="true 只看明确能内推的")
    since_days: int | None = Field(None, ge=1, le=365, description="只看最近多少天发的帖子")


def _post_row(p: Post, r: RawItem, detailed: bool) -> dict[str, Any]:
    info = profile(p, r.raw_text, r.raw_json)
    out = {"id": f"P-{p.id}", "type": p.post_type, "companies": info["body_companies"], "summary": p.summary}
    out.update({"title": info["headline"], "display_title": info["headline"], "roles": p.roles, "job_type": p.job_type,
                "material": info["material"], "material_label": info["material_label"],
                "interview_company": info["interview_company"], "rounds": info["rounds"],
                "body_companies": info["body_companies"], "comment_companies": info["comment_companies"],
                "sources": [source_dict(r.source, r.url, raw_id=r.id, fetched_at=r.fetched_at, published_at=p.published_at)],
                "url": source_dict(r.source, r.url)["url"], "question_count": len(p.interview_questions or [])})
    if p.post_type == "面经":
        out["companies"] = [info["interview_company"]] if info["interview_company"] else []
    for k in ("team", "city", "days_per_week", "duration_months", "can_convert", "interview_rounds"):
        v = getattr(p, k)
        if v is not None:
            out[k] = v
    if p.has_referral:
        out["has_referral"] = True
    if p.published_at:
        out["published"] = p.published_at.date().isoformat()
    if detailed:
        out.update({"roles": p.roles, "likes": p.likes, "raw_id": f"R-{r.id}",
                    "interview_questions": p.interview_questions[:8]})
    return out


def posts_search(args: PostsSearchArgs) -> dict:
    order, note = ranked_ids(args.query, "posts", args.company) if args.query else (None, None)
    with session_scope() as s:
        stmt = select(Post, RawItem).join(RawItem, RawItem.id == Post.raw_item_id)
        stmt = stmt.where(RawItem.is_synthetic.is_(False))
        if args.keyword:
            stmt = stmt.where(Post.summary.ilike(f"%{args.keyword}%") | RawItem.raw_text.ilike(f"%{args.keyword}%"))
        if order is not None:
            stmt = stmt.where(Post.id.in_(order))
        if args.company:
            stmt = stmt.where(_company_filter(Post.companies, Post.big_companies, args.company, is_array=True))
        if args.post_type:
            stmt = stmt.where(Post.post_type == args.post_type)
        if args.has_referral is not None:
            stmt = stmt.where(Post.has_referral.is_(args.has_referral))
        if args.role:
            stmt = stmt.where(Post.roles.any(args.role) | Post.summary.ilike(f"%{args.role}%"))
        if args.job_type:
            stmt = stmt.where(Post.job_type == args.job_type)
        if args.source:
            stmt = stmt.where(RawItem.source == args.source)
        if args.since_days:
            stmt = stmt.where(Post.published_at >= datetime.now(UTC) - timedelta(days=args.since_days))
        if order is None:
            rows = s.execute(stmt.order_by(Post.published_at.desc().nulls_last(), Post.id)).all()
        else:
            rows = _in_order(s.execute(stmt).all(), order, lambda row: row[0].id)
        all_items = [_post_row(p, r, args.response_format == "detailed") for p, r in rows]
        if args.material:
            all_items = [item for item in all_items if item["material"] == args.material]
        if args.company:
            big = match(args.company)
            wanted = big.name if big else args.company
            all_items = [item for item in all_items if wanted in item["companies"] or
                         (item["material"] != "firsthand" and wanted in item["body_companies"])]
        items = all_items[args.offset:args.offset + args.limit]
    has_more = len(all_items) > args.offset + args.limit
    result: dict = {"returned": len(items), "items": items, "offset": args.offset,
                    "next_offset": args.offset + args.limit if has_more else None, "library": library_note("post")}
    if note:
        result["search_note"] = note
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
        details = profile(p, r.raw_text, r.raw_json)["questions"]
        qids = dict(s.execute(select(InterviewQuestion.key, InterviewQuestion.id).where(InterviewQuestion.post_id == pid)).all())
        # 每道题带真实题号（Q-），引用时能生成题目卡片。10-12 回答评测：以前带出处的明细太长，每次都被输出上限整段裁掉，
        # 模型只看到不带题号的题目文字，就顺着别处见过的题号往下推（Q-487 之后写 Q-488）
        out["questions"] = [{"id": f"Q-{qids[q['key']]}" if q["key"] in qids else None, "question": q["question"],
                             "round": q["round"], "category": q["category"]} for q in details]
        out.update({"advice": p.advice, "grad_year": p.grad_year})
        if args.response_format == "detailed":
            out["question_details"] = [dict(q, interview_date=q["interview_date"].isoformat() if q["interview_date"] else None,
                                            published_at=q["published_at"].isoformat() if q["published_at"] else None)
                                       for q in details]
            if r.source == "xhs":  # 图片里的字和评论排在最后，按段截，免得整段丢掉
                from polaris.collectors.xhs_deep import brief

                out["text"] = untrusted(brief(r.raw_text or "", 2500))
            else:
                out["text"] = untrusted(r.raw_text[:2500])
    return out


TOOLS = [
    ToolSpec(name="jobs_trend",
             description="最近一段时间新出现、下架了哪些岗位（按公司、方向或城市分组），来自官网每天的整批重采。"
                         "用户问'最近哪些公司在加招''Agent 岗多了还是少了'时用；结果里的 caveats 必须照实转告。",
             args_schema=JobsTrendArgs, func=jobs_trend, example='{"days": 14, "group_by": "company", "direction": "Agent"}'),
    ToolSpec(name="jobs_search",
             description="查岗位（多来源合并后的）：按公司、城市、类型、方向筛，给 query 时按相关度排，默认只看大厂。"
                         "返回标题、来源及search诊断：scope_empty应停止同范围补查，query_empty允许保持条件有限改写，"
                         "page_exhausted表示翻页越界；不自行放宽用户条件。详情用jobs_get。",
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
