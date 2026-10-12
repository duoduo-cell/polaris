"""结构化抽取：岗位原文 → postings，小红书笔记 → posts。每行记着是哪次模型调用、哪版提示词抽的，可以回放。

抽不到的字段留空，不猜；公司名再用大厂名单归一（字节、抖音、ByteDance 都算字节跳动）。
"""
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select

from polaris.companies import match
from polaris.db.models import Post, Posting, RawItem
from polaris.db.session import session_scope
from polaris.llm import LLM, parallel_map
from polaris.schemas import JobFacts, PostFacts

JOB_PROMPT_VERSION = "extract-job-v1"
POST_PROMPT_VERSION = "extract-post-v4"  # v4：读图片里的字，题目上限 15 → 40（10-12）
POST_CHARS = 12000
MAX_QUESTIONS = 40
DIRECTION_VOCAB = "Agent、RAG、大模型、多模态、推理加速、评测、数据、推荐搜索、后端、前端、客户端、算法、安全、其他"

JOB_SYSTEM = f"""你从招聘原文里抽取岗位信息。规则：
- 只根据原文；原文没写的字段填 null，不要猜。
- 日期一律写成 YYYY-MM-DD；只有月和日时，年份按 2026 年，如果这样算早于 2026-10-10 就按 2027 年。
- 薪资：原文写了才填。salary_text 照抄原文；日薪 salary_unit 填 day，月薪 month，年薪 year；"k" 表示千。
- can_convert：写了有转正机会就是 true，明确写不转正就是 false，没提就是 null。
- job_type：实习填 intern；校招、应届填 campus；社招或要求工作年限填 social。
- directions 从这些里选，可以多选：{DIRECTION_VOCAB}。
- requirements 是任职要求的要点，每条一句，最多 8 条。
- 一条原文里有多个岗位时，只抽第一个。"""

POST_SYSTEM = f"""你从小红书笔记（含评论）里抽取对求职有用的信息。规则：
- 只根据原文；没写的填 null 或空列表，不要猜。
- 小红书上品牌名常被写成谐音或 emoji 躲审核，要还原成规范名称：🫘音→抖音，tou条→今日头条，🍅小说→番茄小说，
  🍉视频→西瓜视频，犷告→广告，鹅厂→腾讯，猪厂→网易，狗东→京东，菊厂→华为；简称也还原（字节→字节跳动，阿里→阿里巴巴）。
- companies 只写这条笔记真正涉及的公司：招人的、面试的、作者在那实习或拿到 offer 的，写规范全称；team 写部门或团队。
  写字楼、园区、地名里带的公司名不算（如"阿里双子塔"只是办公地点）；"BAT""大厂""中厂"这类泛称不要展开成具体公司。
- city 只写城市名，多个用顿号分隔，如"上海、北京"，不加括号说明；发帖属地（IP 属地）不是工作城市。
- directions 从这些里选，可以多选：{DIRECTION_VOCAB}。
- interview_rounds：面经写了"一面""二面"就是对应的轮数（写到几面就填几）。
- interview_questions 只放面经里真实出现的题目（"图片里的文字："之后是截图识别出来的，题目常在那里），
  每条一句，最多 40 条；advice 最多 5 条。
- 正文和评论分开处理：companies、team、roles、面试公司只从正文确定，评论中的另一家公司不属于正文面经。
- 题库、考点清单、备考资料、营销课程归为经验或其他，不冒充亲历面经；爆料可单独分类。
- interview_company 是实际面试公司；company_evidence 照抄正文原句；question_details 与题目对应，证据照抄正文。
  每道题自己的公司、岗位、轮次、面试日期只有正文明确时才填；发布时间不当成面试日期。
- has_referral：发帖人或评论里明确说可以内推才是 true。"""


def _decimal(v: float | None) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def _likes(raw: Any) -> int | None:
    if raw is None:
        return None
    s = str(raw).strip()
    m = re.fullmatch(r"([\d.]+)\s*万", s)
    if m:
        return int(float(m.group(1)) * 10000)
    return int(s) if s.isdigit() else None


def extract_job(llm: LLM, item: RawItem, run_id: int | None = None) -> dict[str, Any] | None:
    r = llm.extract(item.raw_text[:6000], JobFacts, purpose="extract_job", role="extract", system=JOB_SYSTEM,
                    run_id=run_id, prompt_version=JOB_PROMPT_VERSION)
    if not r.ok or r.value is None:
        return None
    f = r.value
    big = match(f.company) or match(item.raw_text[:300])
    return {"raw_item_id": item.id, "company_name": f.company, "title": f.title, "directions": f.directions,
            "jd_text": item.raw_text, "requirements": f.requirements, "city": f.city, "salary_text": f.salary_text,
            "salary_min": _decimal(f.salary_min), "salary_max": _decimal(f.salary_max), "salary_unit": f.salary_unit,
            "job_type": f.job_type, "can_convert": f.can_convert, "posted_at": f.posted_at, "deadline": f.deadline,
            "start_text": f.start_text, "duration_months": f.duration_months, "days_per_week": f.days_per_week,
            "big_company": big.name if big else None, "llm_call_id": r.call_ids[-1],
            "prompt_version": JOB_PROMPT_VERSION, "is_synthetic": item.is_synthetic}


def post_input(text: str) -> str:
    """正文、图片里的字、评论依次放，前面用不完的额度留给后面。
    10-12：图片里的字排在评论后面，按前 6,000 字截断时整段丢了（R-997 九张图约 7,000 字面经，一道题都没进题库）。"""
    body, _, image = text.partition("\n\n图片里的文字：\n")
    main, _, comments = body.partition("\n\n评论：\n")
    parts, left = [main[:POST_CHARS // 2]], POST_CHARS - min(len(main), POST_CHARS // 2)
    for head, part in (("图片里的文字：\n", image), ("评论：\n", comments)):
        if part and left > 0:
            parts.append((head + part)[:left])
            left -= len(parts[-1])
    return "\n\n".join(parts)


def extract_post(llm: LLM, item: RawItem, run_id: int | None = None) -> dict[str, Any] | None:
    r = llm.extract(post_input(item.raw_text), PostFacts, purpose="extract_post", role="extract", system=POST_SYSTEM,
                    run_id=run_id, prompt_version=POST_PROMPT_VERSION)
    if not r.ok or r.value is None:
        return None
    f = r.value
    bigs = [c.name for c in (match(x) for x in [*f.companies, f.team or ""]) if c]
    note = (item.raw_json or {}).get("note") or {}
    published = datetime.fromtimestamp(note["time"] / 1000, UTC) if note.get("time") else item.published_at
    return {"raw_item_id": item.id, "post_type": f.post_type, "companies": f.companies,
            "big_companies": list(dict.fromkeys(bigs)), "team": f.team, "roles": f.roles, "directions": f.directions,
            "city": f.city, "job_type": f.job_type, "grad_year": f.grad_year, "days_per_week": f.days_per_week,
            "duration_months": f.duration_months, "can_convert": f.can_convert, "has_referral": f.has_referral,
            "interview_rounds": f.interview_rounds, "interview_questions": f.interview_questions[:MAX_QUESTIONS],
            "advice": f.advice[:5], "summary": f.summary, "published_at": published,
            "likes": _likes((note.get("interactInfo") or {}).get("likedCount")), "llm_call_id": r.call_ids[-1],
            "evidence_profile": {"interview_company": f.interview_company, "company_evidence": f.company_evidence,
                                 "questions": [q.model_dump(mode="json") for q in f.question_details]},
            "prompt_version": POST_PROMPT_VERSION}


def drop_stale(kind: str, source: str | None = None) -> int:
    """删掉旧提示词版本抽出的结果，让这些条目重新进入待抽。返回删了几条。"""
    target, version = (Posting, JOB_PROMPT_VERSION) if kind == "job" else (Post, POST_PROMPT_VERSION)
    with session_scope() as s:
        stmt = delete(target).where(target.prompt_version.is_distinct_from(version))
        if source:
            stmt = stmt.where(target.raw_item_id.in_(select(RawItem.id).where(RawItem.source == source)))
        return s.execute(stmt).rowcount or 0


def pending(kind: str, limit: int, source: str | None = None) -> list[RawItem]:
    """还没抽过的原始条目。"""
    target = Posting if kind == "job" else Post
    with session_scope() as s:
        done = select(target.raw_item_id)
        stmt = select(RawItem).where(RawItem.kind == kind, RawItem.id.not_in(done)).order_by(RawItem.id).limit(limit)
        if source:
            stmt = stmt.where(RawItem.source == source)
        return list(s.execute(stmt).scalars())


def fit_columns(model: type, row: dict[str, Any]) -> dict[str, Any]:
    """模型偶尔会给某个短字段写一大段（如毕业年份写成一句话），按列宽截断，免得整条存不进去（10-11 踩过）。"""
    out = dict(row)
    for col in model.__table__.columns:
        limit = getattr(col.type, "length", None)
        v = out.get(col.name)
        if limit and isinstance(v, str) and len(v) > limit:
            out[col.name] = v[: limit - 1] + "…"
    return out


def run_extract(kind: str, limit: int, run_id: int | None, concurrency: int, source: str | None = None,
                llm: LLM | None = None) -> dict[str, int]:
    llm = llm or LLM()
    items = pending(kind, limit, source)
    fn = extract_job if kind == "job" else extract_post
    model = Posting if kind == "job" else Post
    results = parallel_map(lambda it: fn(llm, it, run_id), items, concurrency)
    rows = [fit_columns(model, r) for r in results if isinstance(r, dict)]
    saved: list[dict[str, Any]] = []
    for row in rows:  # 一条一条存：一条出错不连累整批
        try:
            with session_scope() as s:
                s.add(model(**row))
            saved.append(row)
        except Exception:  # noqa: BLE001  存不进去的留在待抽里，下次重抽
            pass
    return {"pending": len(items), "extracted": len(saved), "failed": len(items) - len(saved),
            "big_company": sum(1 for r in saved if r.get("big_company") or r.get("big_companies"))}
