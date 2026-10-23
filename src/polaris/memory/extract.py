"""从你的一句话里抽出"候选事实"（模型做），再交给 memory/update.py 按规则决定怎么改记忆（代码做）。

模型只回答"这句话里说了关于你的哪些事、是你亲口说的还是推断的、是不是在改口、从哪天起成立"；
新增、更新、作废、追问这些决定不交给模型，免得同一句话两次跑出不同的记忆。
"""
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from polaris.db.session import session_scope
from polaris.llm.client import LLM
from polaris.memory.schema import ATTRIBUTES
from polaris.memory.update import Candidate, Context, apply, card, render_card
from polaris.report import BEIJING

PROMPT_VERSION = "memory-extract-v6"  # v6：临时请求与个人偏好分开；v5：明确的实习/论文背景。
# 改口要有明确的说法。模型常把"换了个数"也标成改口（10-11 开发集、留出集和实测里都出现过："我每周能去 5 天"被当成改口，
# 直接覆盖了刚说过的 4 天），所以代码再核对一遍：这句话里没有这些词，就不算改口，规则会先问你以哪个为准。
CHANGE_WORDS = re.compile(r"改|变|换|现在|目前|其实|不再|不能|不考虑|算了|还是|说错|更正|纠正|调整|以后|之后|重新|不想|只能")


def has_change_words(message: str) -> bool:
    return CHANGE_WORDS.search(message) is not None
AttrName = Literal[tuple(ATTRIBUTES)]  # type: ignore[valid-type]


class FactCandidate(BaseModel):
    attribute: AttrName = Field(..., description="属性名，只能用属性表里的")  # type: ignore[valid-type]
    qualifier: str | None = Field(None, description="分槽的属性（投递、面试进度）填公司名，其余留空")
    job_ref: str | None = Field(None, description="明确对应一个已展示岗位时填其 J- 编号；不能确定则留空，不能编编号")
    value: str = Field(..., description="简短、规范的值：天数、月数、年份只写数字；城市写城市名；公司写常用名")
    statement: str = Field(..., description="一句话说清这条事实，用第三人称\"用户……\"，如：用户每周能到岗 4 天")
    kind: Literal["stated", "inferred"] = Field(..., description="stated：用户直接说的；inferred：从话里推出来的")
    polarity: Literal["assert", "negate"] = Field("assert", description="negate：用户说这件事不再成立（不考虑了、不想去了）")
    change_signal: bool = Field(False, description="用户明确在改口（改成、现在、其实、换成、不再……）")
    valid_from: date | None = Field(None, description="从哪天起成立；用户说了将来或过去的时间才填，换算成日期")
    valid_to: date | None = Field(None, description="到哪天为止；用户说了才填")
    scope: Literal["profile", "request"] = Field("profile", description="profile用户本人背景/偏好/进度；request仅本次查询条件")
    evidence: str = Field("", description="用户原话中的完整连续分句，包含本人意愿或事实，不能改写、只摘公司/城市词或引用卡片")


class Extraction(BaseModel):
    facts: list[FactCandidate] = Field(default_factory=list)


SYSTEM = """你帮求职助手"北极星"维护对用户的记忆。从用户这一句话里，找出关于用户本人、对找实习有用的事实。
规则：
- 只记用户自己的情况、意愿和进度；用户在问问题、闲聊、问岗位信息时，没有关于自己的事就返回空列表。
- 一句话里有几件事就拆成几条；同一件事不要重复。
- 用户直接说的 kind=stated，包括字面上就能读出来的（"研二"就是硕士在读，"27 届"就是 2027 年毕业）。
- 从话里推出来、用户没明说的 kind=inferred。最常见的：用户说约了、在面某公司的面试，除了面试进度，再加一条
  applied（qualifier 填公司，value 写"投递过"，kind=inferred）——面试说明投过，但用户没亲口说投了。
- 用户说某件事不再成立（不考虑上海了、不想去某公司了），polarity=negate，value 写那件事原来的值。
- 用户在改口（改成、现在、其实、换成、不再、算了还是……），change_signal=true。
- 相对时间按今天的日期换算成具体日期，填进 valid_from / valid_to。
- 不要把卡片里已有的事实原样再抽一遍，除非用户这句话又明确说了它。"""
SYSTEM += """
- 用户明确说的实习经历记 internship_experience、论文情况记 paper_experience。
  “没有实习经历”“没有论文”是明确的背景事实：kind=stated、polarity=assert、value=无实习经历/无论文。
  用户没提不代表没有，不能生成零经历事实。不把这两项放进skill/project或仅作为constraint。
  找第一份实习是目标，不单凭“第一份”推断已无经历；不能把阅读论文或备考当作已有论文。"""
SYSTEM += """
- 本次找/查/推荐某公司、城市、方向的岗位只是request，不是长期偏好，不生成company_like、target_city、target_role等profile事实。
- “以后/长期只考虑”“我想去北京工作”“记住这个偏好”是profile；个人能力、实际可到岗时间、投递/面试进度也属于profile。
- 混合句逐条分开：例如“我会Python，这次只查上海腾讯岗位”，只保存Python，不保存上海/腾讯偏好。
  “我以后优先杭州，今天先查北京”保存杭州，查询北京仅标request。kind=inferred也不能把request升级为待确认偏好。
- 每条候选填scope和evidence；request可返回用于审计，但不会进入个人记忆；没有本人事实时可返回空列表。
"""
SYSTEM += """
- 投递和面试按具体岗位分开；用最近展示的岗位只解决指代，不把推荐、点击、收藏、阅读面经当作用户投递或掌握技术。
- 公司有多个岗位而用户未说明是哪一个时，不猜具体岗位。job_ref 留空。
- 只是在备考某公司的资料，不代表已经投递、收到面试邀请或参加了面试。
- 用户说不想做、不考虑某类岗位或工作（纯算法岗、测试、销售、外包驻场……），记一条 constraint，value 写成"不做纯算法岗"这样；
  同一句里说了想做什么（更想做工程落地），另记 target_role。"""


def _attr_table() -> str:
    return "\n".join(f"- {a.name}：{a.label}（{'可多个' if a.multi else '只有一个'}{'，按公司分' if a.qualified else ''}）"
                     + (f"，例：{a.example}" if a.example else "") for a in ATTRIBUTES.values())


PREFERENCE_ATTRS = {"target_city", "target_role", "company_like", "company_avoid", "constraint", "job_type",
                    "days_per_week", "duration_months", "available_from", "salary_expect"}
REQUEST = re.compile(r"(?:查|搜|找|筛|推荐|看看|对比)[^。！？；;\n]{0,35}(?:岗位|职位|实习|面经)|"
                     r"(?:这次|这轮|今天)[^。！？；;\n]{0,12}(?:只看|只查|只搜)")
PERSONAL = re.compile(r"以后|长期|一直|求职目标|记住|我(?:只)?(?:想去|希望去|打算去|优先|更喜欢|不想去)|我(?:每周|能到岗|可以到岗)")


def _request_reason(f: FactCandidate, message: str) -> str | None:
    if f.scope == "request":
        return "仅本次查询条件，不写入个人偏好或待确认记忆"
    if f.attribute not in PREFERENCE_ATTRS:
        return None
    # 候选证据按分句核对，避免整句有查询动词就删掉同句真实能力/长期偏好。
    if f.evidence and f.evidence in message:
        start = message.index(f.evidence)
        left = max((message.rfind(c, 0, start) for c in "，,。！？；;\n"), default=-1) + 1
        end = start + len(f.evidence)
        right = min((pos for c in "，,。！？；;\n" if (pos := message.find(c, end)) >= 0), default=len(message))
        clause = message[left:right]
        if REQUEST.search(clause) and not PERSONAL.search(clause):
            return "证据分句是查询请求，不是用户本人偏好"
    elif REQUEST.search(message):
        return "查询中的偏好候选缺少可核实的本人表述，不写入记忆"
    return None


def extract(llm: LLM, message: str, card_text: str, today: date, run_id: int | None = None,
            on_request: Callable[[FactCandidate, str], None] | None = None) -> list[Candidate]:
    prompt = (f"今天是 {today.isoformat()}（{'一二三四五六日'[today.weekday()]}）。\n\n属性表：\n{_attr_table()}\n\n"
              f"现在记着的：\n{card_text}\n\n用户这句话：{message}")
    r = llm.extract(prompt, Extraction, purpose="memory_extract", role="extract", system=SYSTEM,
                    prompt_version=PROMPT_VERSION, run_id=run_id, temperature=0)
    if not r.ok or r.value is None:
        raise RuntimeError(f"记忆抽取失败：{r.error}")
    accepted = []
    for f in r.value.facts:
        reason = _request_reason(f, message)
        if reason:
            if on_request:
                on_request(f, reason)
        else:
            accepted.append(f)
    return [Candidate(attribute=f.attribute, value=f.value, statement=f.statement, kind=f.kind, polarity=f.polarity,
                      change_signal=f.change_signal, qualifier=f.qualifier, valid_from=f.valid_from, valid_to=f.valid_to,
                      job_ref=f.job_ref)
            for f in accepted]


def remember(message: str, *, llm: LLM | None = None, now: datetime | None = None, thread_id: str | None = None,
             run_id: int | None = None, session: Session | None = None) -> dict[str, Any]:
    """处理你的一句话：抽候选事实 → 逐条按规则改记忆。返回发生了什么，以及需要问你的话。"""
    now = now or datetime.now(UTC)
    llm = llm or LLM()

    def run(s: Session) -> dict[str, Any]:
        from polaris.memory.job_scope import recent_jobs, resolve, sync_followup

        recent = recent_jobs(s, thread_id)
        context = render_card(card(s, now.astimezone(BEIJING).date()))
        if recent:
            context += "\n最近展示的岗位（只用于解析指代）：\n" + "\n".join(
                f"J-{j.id} {j.company} · {j.title}" for j in recent)
        def audit_request(f: FactCandidate, reason: str) -> None:
            from polaris.db.models import MemoryEvent

            # 与本轮记忆共用事务；只作后台审计，不显示为“记住了”或生成追问。
            s.add(MemoryEvent(op="request_only", attribute=f.attribute, qualifier=f.qualifier, value=f.value,
                              reason=reason, source_text=message, run_id=run_id, thread_id=thread_id, created_at=now))

        candidates = extract(llm, message, context, now.astimezone(BEIJING).date(), run_id, on_request=audit_request)
        ctx = Context(s, now, message, thread_id, run_id)
        events, questions = [], []
        explicit = has_change_words(message)
        for c in candidates:
            job, clarification = resolve(s, c, message, recent)
            if clarification:
                questions.append(clarification)
                # 只说了公司、没说哪个岗位：不猜岗位，但先记在公司这一层，免得用户没回答追问就整条丢了（10-12 探针）
                from polaris.companies import match as big_company

                company = (c.qualifier or "").split("|")[0].strip()
                if "哪个岗位" not in clarification or not company:
                    continue
                company = big.name if (big := big_company(company)) else company
                c.qualifier, c.job_ref = company, None
                e, q = apply(ctx, c)
                e["scope_label"] = f"{company}（具体岗位待确认）"
                events.append(e)
                if q:
                    questions.append(q)
                s.flush()
                continue
            c.change_signal = c.change_signal and explicit
            e, q = apply(ctx, c)
            if job:
                e["scope_label"] = f"{job.company} · {job.title}"
            events.append(e)
            if q:
                questions.append(q)
            s.flush()
            if job:
                sync_followup(s, job, c, e, message)
        return {"events": events, "questions": questions}

    if session is not None:
        return run(session)
    with session_scope() as s:
        return run(s)
