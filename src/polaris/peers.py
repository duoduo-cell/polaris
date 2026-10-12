"""帖子里的"人和结果"（10-12）：拿到或没拿到某个岗位的人是什么背景。

求职者最想对照的是"跟我同阶段、拿到这个岗位的人是什么水平"。这些信息散在小红书和脉脉的帖子正文、图片和评论里，
原来的抽取只抽公司、岗位、面试题，不抽人。这里单独抽一遍：模型只照原文填，没写的留空，每条带原句，能点回原帖核对。
"""
import re
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from polaris.companies import match
from polaris.db.models import PeerProfile, RawItem
from polaris.db.session import session_scope
from polaris.extract import fit_columns
from polaris.llm.client import LLM, parallel_map

PROMPT_VERSION = "peer-v3"  # v2：学校只照抄、不展开缩写（v1 把"山财"写成了山西财经大学）；
# v3：转述、爆料、面经搬运号不记（v2 抽查 20 条里有 1 条爆料）
MAX_CHARS = 9000
# 正文、图片或评论里像是在讲某个人的背景或结果，才值得抽
SIGNALS = re.compile(r"offer|上岸|oc|意向|bg|背景|简历|履历|论文|ccf|顶会|一作|实习经历|段实习|一面|二面|三面|终面|hr面|"
                     r"转正|挂了|凉了|本科|硕士|博士|研一|研二|研三|985|211|双非|海本|c9", re.I)


class Paper(BaseModel):
    venue: str | None = Field(None, description="会议或期刊名，如 ACL、NeurIPS；原文没写留空")
    level: Literal["CCF-A", "CCF-B", "CCF-C", "顶会", "SCI", "其他", "未说明"] = Field(
        "未说明", description="原文写了 CCF-A/B/C、顶会、SCI 才填；会议名能确定是 CCF-A 的（如 ACL、NeurIPS、ICML、CVPR、KDD）填 CCF-A")
    position: str | None = Field(None, description="一作、共一、二作、通讯……原文没写留空")
    status: str | None = Field(None, description="中稿、在投、arXiv……原文没写留空")


class Internship(BaseModel):
    company: str | None = None
    team: str | None = None
    role: str | None = None
    months: int | None = Field(None, description="几个月，原文没写留空")


class Person(BaseModel):
    who: Literal["作者", "评论区"] = Field(..., description="发帖人本人，还是评论区里自述背景的人")
    stage: Literal["本科", "硕士", "博士", "已工作", "未说明"]
    grad_year: str | None = Field(None, description="几届，如 2027届；原文没写留空")
    school: str | None = Field(None, description="原文写的学校，或“985/双非/海本/C9”这类说法，照抄")
    major: str | None = None
    papers: list[Paper] = Field(default_factory=list, description="每篇一条；原文只写“两篇 CCF-A”就写两条")
    paper_summary: str | None = Field(None, description="原文对论文的概括，照抄，如“2 篇 CCF-A 一作”")
    internships: list[Internship] = Field(default_factory=list, description="以前的实习，每段一条")
    awards: list[str] = Field(default_factory=list, description="竞赛、奖项，每条一句，最多 5 条")
    target_company: str | None = Field(None, description="这次投的或拿到的公司")
    target_team: str | None = Field(None, description="团队或部门，如 Seed、豆包、TEG")
    target_role: str | None = None
    job_type: Literal["日常实习", "暑期实习", "校招", "社招", "未说明"] = "未说明"
    direction: str | None = Field(None, description="方向，如 Agent、后训练、RAG、推荐")
    outcome: Literal["拿到offer", "挂了", "进行中", "放弃", "未说明"] = "未说明"
    reached: str | None = Field(None, description="面到哪一轮，如 二面、HR 面")
    difficulty: str | None = Field(None, description="原文对难度、压力的说法，照抄一句，没有留空")
    dates: str | None = Field(None, description="原文里的面试、OC 日期，照抄")
    evidence: str = Field(..., description="支持这条记录的原句，照抄，至少包含一句背景或结果")
    from_image: bool = Field(False, description="主要信息是不是来自“图片里的文字”那一段")


class PeerFacts(BaseModel):
    people: list[Person] = Field(default_factory=list, description="没有具体个人背景或结果就给空列表")


SYSTEM = """你从求职帖子（小红书、脉脉）里找"具体某个人"的求职背景和结果。
- 只记录原文明确写到的人：发帖人自己，或评论区里讲自己背景、结果的人。招聘要求、泛泛建议不算。
- 转述别人的不记：爆料、"他/她/我朋友/某同学拿到了……"、员工转述候选人的，一律不记。
- 面经搬运号、培训机构号（只罗列题目，正文没有发帖人自己的经历）不记。
- 一个人至少要有一项背景（阶段、学校、论文、实习、奖项）或一项结果（拿到 offer、挂在几面、进行中），否则不记。
- 每个字段只照原文填，没写就留空或填"未说明"，不推测、不补全。学校只照抄原文的写法，缩写不要展开（"山财"就写"山财"）。
- 论文按篇记；只写"两篇 CCF-A"就记两条 level=CCF-A。
- evidence 照抄原文里支持这条记录的句子（可以是图片里的文字）。
- 正文里"图片里的文字："之后是从图片识别出来的，信息主要来自那里时 from_image=true。"""


def compose(raw: RawItem) -> str:
    """给模型看的原文：标题和正文、图片里的文字优先，评论放后面，总长有上限。"""
    text = raw.raw_text or ""
    if "图片里的文字：" in text and len(text) > MAX_CHARS:
        body, image = text.split("图片里的文字：", 1)
        image = image[:MAX_CHARS // 2]
        return body[:MAX_CHARS - len(image)] + "\n图片里的文字：" + image
    return text[:MAX_CHARS]


def pending(limit: int, source: str | None = None, redo: bool = False, ids: list[int] | None = None) -> list[RawItem]:
    """还没按当前版本抽过、正文里像有"人和结果"的帖子。"""
    with session_scope() as s:
        done = select(PeerProfile.raw_item_id).where(PeerProfile.prompt_version == PROMPT_VERSION)
        stmt = select(RawItem).where(RawItem.kind == "post", RawItem.is_synthetic.is_(False))
        if not redo:  # 抽过的（有人的有记录；没人的在原始数据上记了版本号）不再抽
            stmt = stmt.where(RawItem.id.not_in(done),
                              RawItem.raw_json["peer_version"].astext.is_distinct_from(PROMPT_VERSION))
        if source:
            stmt = stmt.where(RawItem.source == source)
        if ids is not None:
            stmt = stmt.where(RawItem.id.in_(ids))
        rows = s.execute(stmt.order_by(RawItem.published_at.desc().nulls_last())).scalars()
        items = [r for r in rows if SIGNALS.search(r.raw_text or "")]
        for r in items:
            s.expunge(r)
    return items[:limit]


def _top(p: Paper) -> bool:
    return p.level in ("CCF-A", "顶会")


def extract_one(llm: LLM, raw: RawItem) -> list[dict[str, Any]] | None:
    r = llm.extract(compose(raw), PeerFacts, purpose="extract_peer", role="extract", system=SYSTEM,
                    prompt_version=PROMPT_VERSION)
    if not r.ok or r.value is None:
        return None
    rows = []
    for p in r.value.people:
        big = match(p.target_company or p.target_team or "") if (p.target_company or p.target_team) else None
        rows.append({"raw_item_id": raw.id, "source": raw.source, "published_at": raw.published_at, "who": p.who,
                     "stage": p.stage, "grad_year": p.grad_year, "school": p.school, "major": p.major,
                     "papers": [x.model_dump() for x in p.papers], "paper_summary": p.paper_summary,
                     "top_papers": sum(1 for x in p.papers if _top(x)),
                     "internships": [x.model_dump() for x in p.internships], "awards": p.awards[:5],
                     "target_company": p.target_company, "big_company": big.name if big else None,
                     "target_team": p.target_team, "target_role": p.target_role, "job_type": p.job_type,
                     "direction": p.direction, "outcome": p.outcome, "reached": p.reached, "difficulty": p.difficulty,
                     "dates": p.dates, "evidence": p.evidence, "from_image": p.from_image,
                     "llm_call_id": r.call_ids[-1], "prompt_version": PROMPT_VERSION})
    return rows


def run(limit: int, concurrency: int = 8, source: str | None = None, redo: bool = False,
        ids: list[int] | None = None) -> dict[str, int]:
    """抽一批。重抽某条时先删它的旧记录。返回：看了几条帖子、几条里有人、一共几个人。"""
    from polaris.config import get_settings
    from polaris.db.models import PeerProfile as P

    llm = LLM()
    items = pending(limit, source, redo, ids)
    results = parallel_map(lambda it: (it.id, extract_one(llm, it)), items, concurrency or get_settings().llm_concurrency)
    posts = people = failed = 0
    for res in results:
        if not isinstance(res, tuple) or res[1] is None:
            failed += 1
            continue
        rid, rows = res
        try:  # 一条存不进去（比如模型把团队写成一大段）不连累整批；按列宽截断，见 extract.fit_columns
            with session_scope() as s:
                s.query(P).filter(P.raw_item_id == rid).delete()
                for row in rows:
                    s.add(P(**fit_columns(P, row)))
                raw = s.get(RawItem, rid)  # 没抽出人也记一笔，免得每轮都重抽同一批（10-12 发现）
                raw.raw_json = {**(raw.raw_json or {}), "peer_version": PROMPT_VERSION}
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        posts += bool(rows)
        people += len(rows)
    return {"read": len(items), "with_people": posts, "people": people, "failed": failed}
