"""memory_*：北极星对你的记忆——看、查历史、查某天记着什么、忘掉。记忆的写入不靠模型调工具：你每说一句话，
memory/extract.py 先抽候选事实、memory/update.py 按规则改记忆；这里的工具让 agent（和你）能看、能改。

对标：Muse 的记忆文件能看、能改、能下载、能让它忘掉；Dots 的记忆看不到单条。北极星每条都带"从哪句话来、什么时候成立、
什么时候被取代"（`docs/archive/PLAN.md` 0.0 第 3 条）。
"""
from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from polaris.db.models import MemoryFact
from polaris.db.session import session_scope
from polaris.memory.schema import ATTRIBUTES, qualifier_key
from polaris.memory.update import card, forget, known_at, pending
from polaris.report import BEIJING
from polaris.tools.base import ToolError, ToolSpec

AttrName = Literal[tuple(ATTRIBUTES)]  # type: ignore[valid-type]
ATTR_HELP = "属性：" + "、".join(f"{a.name}（{a.label}）" for a in ATTRIBUTES.values())


def _fact(f: MemoryFact, detailed: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"id": f"M-{f.id}", "attribute": ATTRIBUTES[f.attribute].label, "value": f.value}
    if f.qualifier:
        out["for"] = f.qualifier
    if f.valid_from:
        out["valid_from"] = f.valid_from.isoformat()
    if f.valid_to:
        out["valid_to"] = f.valid_to.isoformat()
    if detailed:
        out.update({"status": f.status, "kind": "你说的" if f.kind == "stated" else "推断的",
                    "recorded": f.recorded_at.astimezone(BEIJING).date().isoformat(), "from_text": f.source_text[:120]})
        if f.superseded_at:
            out["superseded"] = f.superseded_at.astimezone(BEIJING).date().isoformat()
    return out


class MemoryCardArgs(BaseModel):
    on: date | None = Field(None, description="看哪一天成立的情况，默认今天（如 2026-11-15：那天起才成立的安排也会算上）")


def memory_card(args: MemoryCardArgs) -> dict:
    on = args.on or datetime.now(BEIJING).date()
    with session_scope() as s:
        facts = [_fact(f) for f in card(s, on)]
        waiting = [{"id": f"M-{f.id}", "statement": f.statement} for f in pending(s)]
    out: dict[str, Any] = {"on": on.isoformat(), "facts": facts}
    if waiting:
        out["waiting_for_confirmation"] = waiting
    if not facts:
        out["hint"] = "还没有记住关于用户的事。用户问到自己的情况时，如实说不知道。"
    return out


class MemoryHistoryArgs(BaseModel):
    attribute: AttrName = Field(..., description=ATTR_HELP)  # type: ignore[valid-type]
    qualifier: str | None = Field(None, description="按公司分的属性（applied、interview_stage）填公司名")


def memory_history(args: MemoryHistoryArgs) -> dict:
    with session_scope() as s:
        stmt = select(MemoryFact).where(MemoryFact.attribute == args.attribute)
        if args.qualifier:
            stmt = stmt.where(MemoryFact.qualifier == qualifier_key(args.qualifier))
        facts = [_fact(f, True) for f in s.execute(stmt.order_by(MemoryFact.recorded_at, MemoryFact.id)).scalars()]
    out: dict[str, Any] = {"attribute": ATTRIBUTES[args.attribute].label, "versions": facts}
    if not facts:
        out["hint"] = "这个属性从来没有记过。用户问到时，如实说没听用户说过。"
    return out


class MemoryKnownAtArgs(BaseModel):
    day: date = Field(..., description="哪一天（那天结束时北极星记着什么）")


def memory_known_at(args: MemoryKnownAtArgs) -> dict:
    at = datetime.combine(args.day, time.max, tzinfo=BEIJING)
    with session_scope() as s:
        facts = [_fact(f) for f in known_at(s, at)]
    return {"day": args.day.isoformat(), "facts": facts,
            **({"hint": "那天还没有记住任何事。"} if not facts else {})}


class MemoryForgetArgs(BaseModel):
    attribute: AttrName = Field(..., description=ATTR_HELP)  # type: ignore[valid-type]
    qualifier: str | None = Field(None, description="按公司分的属性填公司名")
    value: str | None = Field(None, description="只忘掉这个值；不填就忘掉这个属性的全部")


def memory_forget(args: MemoryForgetArgs) -> dict:
    with session_scope() as s:
        out = forget(s, args.attribute, args.qualifier, args.value, source_text="用户要求忘掉")
    if not out:
        raise ToolError("没有找到要忘掉的记忆", "先用 memory_card 或 memory_history 看看记着什么", '{"attribute": "school"}')
    return {"forgotten": len(out), "note": "已作废并记下：以后不会再推断出同样的内容。旧记录留档，可在 memory_history 里看到。"}


TOOLS = [
    ToolSpec(name="memory_card", description="看北极星记着的关于用户的事（方向、城市、到岗时间、投递和面试进度等），每条带编号 M-；"
                                             "还有等用户确认的推断。回答和用户自己有关的问题前先看它。",
             args_schema=MemoryCardArgs, func=memory_card, example="{}"),
    ToolSpec(name="memory_history", description="某个属性的全部历史版本：什么时候记下、从哪句话来、什么时候被新说法取代。"
                                                "用户问\"我之前是怎么说的\"\"什么时候改的\"用它。",
             args_schema=MemoryHistoryArgs, func=memory_history, example='{"attribute": "days_per_week"}'),
    ToolSpec(name="memory_known_at", description="某一天北极星记着的全部事实（按当时的记忆）。用户问\"我九月底时说的是什么\"用它。",
             args_schema=MemoryKnownAtArgs, func=memory_known_at, example='{"day": "2026-09-30"}'),
    ToolSpec(name="memory_forget", description="用户要求忘掉某件事时用：作废这条记忆，并且以后不再推断出同样的内容。",
             args_schema=MemoryForgetArgs, func=memory_forget, read_only=False, example='{"attribute": "school"}'),
]
