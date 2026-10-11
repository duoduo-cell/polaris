"""从你的一句话里抽出"候选事实"（模型做），再交给 memory/update.py 按规则决定怎么改记忆（代码做）。

模型只回答"这句话里说了关于你的哪些事、是你亲口说的还是推断的、是不是在改口、从哪天起成立"；
新增、更新、作废、追问这些决定不交给模型，免得同一句话两次跑出不同的记忆。
"""
import re
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from polaris.db.session import session_scope
from polaris.llm.client import LLM
from polaris.memory.schema import ATTRIBUTES
from polaris.memory.update import Candidate, Context, apply, card, render_card

PROMPT_VERSION = "memory-extract-v4"  # v4：不想做某类岗位记成硬条件（10-12 探针里"不想做纯算法岗"丢了）
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
- 投递和面试按具体岗位分开；用最近展示的岗位只解决指代，不把推荐、点击、收藏、阅读面经当作用户投递或掌握技术。
- 公司有多个岗位而用户未说明是哪一个时，不猜具体岗位。job_ref 留空。
- 只是在备考某公司的资料，不代表已经投递、收到面试邀请或参加了面试。
- 用户说不想做、不考虑某类岗位或工作（纯算法岗、测试、销售、外包驻场……），记一条 constraint，value 写成"不做纯算法岗"这样；
  同一句里说了想做什么（更想做工程落地），另记 target_role。"""


def _attr_table() -> str:
    return "\n".join(f"- {a.name}：{a.label}（{'可多个' if a.multi else '只有一个'}{'，按公司分' if a.qualified else ''}）"
                     + (f"，例：{a.example}" if a.example else "") for a in ATTRIBUTES.values())


def extract(llm: LLM, message: str, card_text: str, today: date, run_id: int | None = None) -> list[Candidate]:
    prompt = (f"今天是 {today.isoformat()}（{'一二三四五六日'[today.weekday()]}）。\n\n属性表：\n{_attr_table()}\n\n"
              f"现在记着的：\n{card_text}\n\n用户这句话：{message}")
    r = llm.extract(prompt, Extraction, purpose="memory_extract", role="extract", system=SYSTEM,
                    prompt_version=PROMPT_VERSION, run_id=run_id, temperature=0)
    if not r.ok or r.value is None:
        raise RuntimeError(f"记忆抽取失败：{r.error}")
    return [Candidate(attribute=f.attribute, value=f.value, statement=f.statement, kind=f.kind, polarity=f.polarity,
                      change_signal=f.change_signal, qualifier=f.qualifier, valid_from=f.valid_from, valid_to=f.valid_to,
                      job_ref=f.job_ref)
            for f in r.value.facts]


def remember(message: str, *, llm: LLM | None = None, now: datetime | None = None, thread_id: str | None = None,
             run_id: int | None = None, session: Session | None = None) -> dict[str, Any]:
    """处理你的一句话：抽候选事实 → 逐条按规则改记忆。返回发生了什么，以及需要问你的话。"""
    now = now or datetime.now(UTC)
    llm = llm or LLM()

    def run(s: Session) -> dict[str, Any]:
        from polaris.memory.job_scope import recent_jobs, resolve, sync_followup

        recent = recent_jobs(s, thread_id)
        context = render_card(card(s, now.date()))
        if recent:
            context += "\n最近展示的岗位（只用于解析指代）：\n" + "\n".join(
                f"J-{j.id} {j.company} · {j.title}" for j in recent)
        candidates = extract(llm, message, context, now.date(), run_id)
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
