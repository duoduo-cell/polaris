"""记忆更新（M5，北极星自己实现，`TECH.md` 第 3 节环节 5）。

模型只负责从你的话里抽出"候选事实"（memory/extract.py）；**怎么改记忆由这里的规则决定**，可测试、可复现：

| 情况 | 决定 |
|---|---|
| 你亲口说的正好是待确认里的那个值（回答了"对吗""以哪个为准"） | confirm：待确认的生效；你否认待确认的值 → reject |
| 推断出来的（不是你亲口说的），你否认过同样的推断 | suppressed：不再提（参考 WeKnora 的抑制记录） |
| 推断出来的 | pending：先进"待确认"，问你一句，确认了才生效（参考 WeKnora） |
| 和记着的一样 | noop：只记下"你又提到了" |
| 多值属性（会的技术、想去的城市）出现新值，或者单值的槽还空着 | add |
| 你说"不再""不考虑了" | invalidate：作废那条，旧记录留着 |
| 单值的槽已有别的值，新说法从将来某天起才成立（"下个月起每周 3 天"） | schedule：旧的到那天为止，新的从那天起 |
| 单值的槽已有别的值：属性本来就会往前走（面试进度）/ 你明确说改了 / 旧的是推断 / 旧的超过 30 天没再提 | update：旧的被取代 |
| 单值的槽已有别的值，你最近才说过旧的，也没说要改 | ask：不覆盖，先问你以哪个为准（新说法进"待确认"） |

双时间线：valid_from / valid_to 是事情在现实里什么时候成立，recorded_at / superseded_at 是北极星什么时候知道、什么时候被取代。
所以既能回答"我现在每周能去几天"，也能回答"我九月份是怎么说的"。每个决定都写进 memory_events。
"""
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from polaris.db.models import MemoryEvent, MemoryFact, MemorySuppression
from polaris.memory.schema import ATTRIBUTES, qualifier_key, value_key

STALE_DAYS = 30
# 按公司分的属性互相牵连：你否认投过某公司时，这家的"面试进度"推断也一并作废（面试意味着投过）
LINKED = {"applied": ("interview_stage",), "interview_stage": ("applied",)}


def _lock_writer(s: Session) -> None:
    # 不同聊天可并行抽取和检索，但共享的个人记忆只能有一个事务写入。
    s.execute(text("select pg_advisory_xact_lock(739514623)"))


@dataclass
class Candidate:
    attribute: str
    value: str
    statement: str
    kind: str = "stated"  # stated / inferred
    polarity: str = "assert"  # assert / negate
    change_signal: bool = False
    qualifier: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    job_ref: str | None = None


@dataclass
class Decision:
    op: str
    reason: str
    old: list[MemoryFact] = field(default_factory=list)
    question: str | None = None


def decide(c: Candidate, slot: list[MemoryFact], suppressed: bool, now: datetime,
           waiting: list[MemoryFact] | None = None) -> Decision:
    """纯规则：c 是候选，slot 是同一槽位里现在生效的事实，waiting 是同槽位待确认的。"""
    attr = ATTRIBUTES[c.attribute]
    vk = value_key(c.attribute, c.value)
    same = [f for f in slot if f.value_key == vk]
    asked = [f for f in waiting or [] if f.value_key == vk]
    if asked and c.kind == "stated":
        if c.polarity == "negate":
            return Decision("reject", "你否认了待确认的这条", asked)
        return Decision("confirm", "你确认了待确认的这条", asked)
    if waiting and c.kind == "stated" and attr.qualified and c.polarity == "assert" and not same:
        # 按公司分的槽里有一条待确认的推断（"字节约了一面"→ 推断投过字节），你接着说了具体的（"投的是 Agent 开发实习"）：
        # 当作确认，并把说法补全成你说的
        return Decision("confirm", "你确认了待确认的这条，并补全了说法", waiting[:1])
    if c.polarity == "negate":
        targets = same or ([] if attr.multi else slot)
        if not targets:
            return Decision("noop", "要作废的说法本来就不在记忆里")
        return Decision("invalidate", "你说这条不再成立", targets)
    if c.kind == "inferred":
        if suppressed:
            return Decision("suppressed", "你否认过同样的推断，不再提")
        if same:
            return Decision("noop", "已经记着，不用再推断")
        if any(f.value_key == vk for f in waiting or []):  # 测试集 v2 跑完后加：同样的推断已经在等你确认，不再重复问
            return Decision("noop", "同样的推断已经在等你确认")
        return Decision("pending", "是推断出来的，先等你确认", question=f"我理解的是：{c.statement}。对吗？")
    if same and waiting and not attr.multi and c.polarity == "assert":
        # 问你"以哪个为准"时你重申了原来的（"还是 5 天吧"）：待确认的新说法作废
        return Decision("reject", "你重申了原来的说法，待确认的那条作废", waiting[:1])
    if same:
        return Decision("noop", "和记着的一样", same)
    if attr.multi or not slot:
        return Decision("add", "新信息")
    if attr.evolves:  # 进度类的变化在说的时候就已经发生了（"一面过了，下周二面"：现在就是"等二面"），不排到将来
        return Decision("update", f"{attr.label}往前走了", slot)
    today = now.date()
    if c.valid_from and c.valid_from > today:
        return Decision("schedule", f"从 {c.valid_from.isoformat()} 起按新的，之前仍按旧的", slot)
    if c.change_signal:
        return Decision("update", "你明确说改了", slot)
    if all(f.kind == "inferred" for f in slot):
        return Decision("update", "旧的是推断，以你亲口说的为准", slot)
    if all((now - f.last_confirmed_at).days >= STALE_DAYS for f in slot):
        return Decision("update", f"旧说法超过 {STALE_DAYS} 天没再提，按新的", slot)
    olds = "、".join(f"「{f.statement}」" for f in slot)
    return Decision("ask", "和你最近的说法对不上，先问清楚", slot,
                    question=f"你之前说{olds}，刚才说「{c.statement}」，以哪个为准？")


@dataclass
class Context:
    session: Session
    now: datetime
    source_text: str
    thread_id: str | None = None
    run_id: int | None = None


def _event(ctx: Context, op: str, c_attr: str, qualifier: str | None, value: str | None, reason: str,
           fact: MemoryFact | None = None, old: MemoryFact | None = None) -> dict[str, Any]:
    e = MemoryEvent(op=op, attribute=c_attr, qualifier=qualifier, value=value, fact_id=fact.id if fact else None,
                    old_fact_id=old.id if old else None, reason=reason, source_text=ctx.source_text,
                    thread_id=ctx.thread_id, run_id=ctx.run_id, created_at=ctx.now)
    ctx.session.add(e)
    return {"op": op, "attribute": c_attr, "qualifier": qualifier, "value": value, "reason": reason,
            "label": ATTRIBUTES[c_attr].label, "old_value": old.value if old else None,
            "fact_id": fact.id if fact else None, "old_fact_id": old.id if old else None}


def _new_fact(ctx: Context, c: Candidate, status: str, supersedes: MemoryFact | None = None) -> MemoryFact:
    f = MemoryFact(attribute=c.attribute, qualifier=qualifier_key(c.qualifier), value=c.value,
                   value_key=value_key(c.attribute, c.value), statement=c.statement, kind=c.kind, status=status,
                   valid_from=c.valid_from, valid_to=c.valid_to, recorded_at=ctx.now, last_confirmed_at=ctx.now,
                   supersedes_id=supersedes.id if supersedes else None, source_text=ctx.source_text,
                   thread_id=ctx.thread_id, run_id=ctx.run_id)
    ctx.session.add(f)
    ctx.session.flush()
    return f


def slot_facts(s: Session, attribute: str, qualifier: str | None, statuses: tuple[str, ...] = ("active",)) -> list[MemoryFact]:
    q = qualifier_key(qualifier)
    stmt = select(MemoryFact).where(MemoryFact.attribute == attribute, MemoryFact.status.in_(statuses))
    stmt = stmt.where(MemoryFact.qualifier == q) if q else stmt.where(MemoryFact.qualifier.is_(None))
    return list(s.execute(stmt.order_by(MemoryFact.id)).scalars())


def company_key(qualifier: str | None) -> str | None:
    """按公司分的槽，抑制记录按公司记（"@腾讯"）：否认过就整家公司不再推断，不管下次推断的说法怎么写。"""
    q = qualifier_key(qualifier)
    return f"@{q}" if q else None


def is_suppressed(s: Session, attribute: str, vk: str, qualifier: str | None = None) -> bool:
    keys = [vk] + ([company_key(qualifier)] if ATTRIBUTES[attribute].qualified and qualifier else [])
    return s.execute(select(MemorySuppression.id).where(MemorySuppression.attribute == attribute,
                                                       MemorySuppression.value_key.in_(keys))).first() is not None


def _suppress(s: Session, attribute: str, key: str, reason: str) -> None:
    if not s.execute(select(MemorySuppression.id).where(MemorySuppression.attribute == attribute,
                                                       MemorySuppression.value_key == key)).first():
        s.add(MemorySuppression(attribute=attribute, value_key=key, reason=reason))
        s.flush()


def apply(ctx: Context, c: Candidate) -> tuple[dict[str, Any], str | None]:
    """按规则改记忆，返回（事件，要问你的话）。"""
    if c.attribute not in ATTRIBUTES:
        return {"op": "skip", "attribute": c.attribute, "reason": "不在属性表里"}, None
    s, now = ctx.session, ctx.now
    _lock_writer(s)
    q = qualifier_key(c.qualifier)
    slot = slot_facts(s, c.attribute, q)
    if not ATTRIBUTES[c.attribute].multi:  # 单值槽：只看"现在或将来还成立"的那几条
        slot = [f for f in slot if f.valid_to is None or f.valid_to > now.date()]
    d = decide(c, slot, is_suppressed(s, c.attribute, value_key(c.attribute, c.value), q), now,
               slot_facts(s, c.attribute, q, ("pending",)))
    if c.polarity == "negate" and q and c.kind == "stated":
        # 你否认了这家公司的某件事：同公司牵连属性里待确认的推断一并作废（"我没投腾讯" → "在面腾讯"的推断也不成立）
        for attr_name in LINKED.get(c.attribute, ()):
            for f in slot_facts(s, attr_name, q, ("pending",)):
                confirm(s, f.id, False, now, ctx.source_text)
    if d.op in ("confirm", "reject"):
        f = d.old[0]
        if d.op == "confirm" and f.value_key != value_key(c.attribute, c.value):
            f.value, f.value_key, f.statement = c.value, value_key(c.attribute, c.value), c.statement
        return confirm(s, f.id, d.op == "confirm", now, ctx.source_text), None
    if d.op == "noop":
        for f in d.old:
            f.last_confirmed_at = now
        return _event(ctx, "noop", c.attribute, q, c.value, d.reason, d.old[0] if d.old else None), None
    if d.op in ("suppressed",):
        return _event(ctx, d.op, c.attribute, q, c.value, d.reason), None
    if d.op == "invalidate":
        for f in d.old:
            f.status, f.superseded_at, f.valid_to = "retracted", now, f.valid_to or now.date()
        return _event(ctx, "invalidate", c.attribute, q, c.value, d.reason, old=d.old[0]), None
    if d.op == "add":
        f = _new_fact(ctx, c, "active")
        return _event(ctx, "add", c.attribute, q, c.value, d.reason, f), None
    if d.op == "pending":
        f = _new_fact(ctx, c, "pending")
        return _event(ctx, "pending", c.attribute, q, c.value, d.reason, f), d.question
    if d.op == "ask":
        f = _new_fact(ctx, c, "pending", supersedes=d.old[0])
        return _event(ctx, "ask", c.attribute, q, c.value, d.reason, f, d.old[0]), d.question
    if d.op == "schedule":
        for old in d.old:
            old.valid_to = c.valid_from
        f = _new_fact(ctx, c, "active", supersedes=d.old[0])
        return _event(ctx, "schedule", c.attribute, q, c.value, d.reason, f, d.old[0]), None
    # update
    if ATTRIBUTES[c.attribute].evolves:  # 进度从现在起就是新的（模型可能把"下周二面"的日期填进 valid_from）
        c.valid_from = None
    for old in d.old:
        old.status, old.superseded_at = "superseded", now
        old.valid_to = old.valid_to if old.valid_to and old.valid_to < now.date() else (c.valid_from or now.date())
    f = _new_fact(ctx, c, "active", supersedes=d.old[0])
    return _event(ctx, "update", c.attribute, q, c.value, d.reason, f, d.old[0]), None


def confirm(s: Session, fact_id: int, accept: bool, now: datetime | None = None, source_text: str = "") -> dict[str, Any]:
    """你回答了"对吗""以哪个为准"：确认就生效（顶掉同槽的旧值），否认就作废；否认的是推断时记一条抑制。"""
    _lock_writer(s)
    now = now or datetime.now(UTC)
    f = s.get(MemoryFact, fact_id, populate_existing=True)
    if f is None or f.status != "pending":
        raise ValueError(f"没有待确认的记忆 #{fact_id}")
    ctx = Context(s, now, source_text or f.source_text, f.thread_id)
    if not accept:
        f.status, f.superseded_at = "retracted", now
        if f.kind == "inferred":
            _suppress(s, f.attribute, f.value_key, "你否认了这条推断")
            if f.qualifier and ATTRIBUTES[f.attribute].qualified:
                for attr_name in (f.attribute, *LINKED.get(f.attribute, ())):
                    _suppress(s, attr_name, f"@{f.qualifier}", f"你否认了和{f.qualifier}有关的推断")
        return _event(ctx, "reject", f.attribute, f.qualifier, f.value, "你说不对", f)
    if not ATTRIBUTES[f.attribute].multi:
        for old in slot_facts(s, f.attribute, f.qualifier):
            if old.id != f.id:
                old.status, old.superseded_at = "superseded", now
                old.valid_to = old.valid_to or now.date()
    f.status, f.kind, f.last_confirmed_at = "active", "stated", now
    return _event(ctx, "confirm", f.attribute, f.qualifier, f.value, "你确认了", f)


def forget(s: Session, attribute: str, qualifier: str | None = None, value: str | None = None,
           now: datetime | None = None, source_text: str = "") -> list[dict[str, Any]]:
    """你要求忘掉：作废匹配的事实（含待确认的），并记抑制，以后不再推断出同样的内容。"""
    _lock_writer(s)
    now = now or datetime.now(UTC)
    facts = slot_facts(s, attribute, qualifier, ("active", "pending")) if qualifier or ATTRIBUTES[attribute].qualified \
        else list(s.execute(select(MemoryFact).where(MemoryFact.attribute == attribute,
                                                     MemoryFact.status.in_(("active", "pending")))).scalars())
    if value:
        facts = [f for f in facts if f.value_key == value_key(attribute, value)]
    out = []
    ctx = Context(s, now, source_text)
    for f in facts:
        f.status, f.superseded_at, f.valid_to = "retracted", now, f.valid_to or now.date()
        _suppress(s, f.attribute, f.value_key, "你要求忘掉")
        out.append(_event(ctx, "forget", f.attribute, f.qualifier, f.value, "你要求忘掉", old=f))
    return out


# ---- 读 ----

def card(s: Session, on: date | None = None) -> list[MemoryFact]:
    """某一天成立的事实（默认今天）：你的"卡片"，常驻在对话上下文里。"""
    on = on or datetime.now(UTC).date()
    stmt = select(MemoryFact).where(MemoryFact.status == "active",
                                    or_(MemoryFact.valid_from.is_(None), MemoryFact.valid_from <= on),
                                    or_(MemoryFact.valid_to.is_(None), MemoryFact.valid_to > on))
    order = list(ATTRIBUTES)
    return sorted(s.execute(stmt).scalars(), key=lambda f: (order.index(f.attribute), f.qualifier or "", f.id))


def known_at(s: Session, at: datetime) -> list[MemoryFact]:
    """在 at 那一刻北极星记着的（不含待确认）：回答"我当时是怎么说的"。"""
    stmt = select(MemoryFact).where(MemoryFact.status != "pending", MemoryFact.recorded_at <= at,
                                    or_(MemoryFact.superseded_at.is_(None), MemoryFact.superseded_at > at))
    return list(s.execute(stmt.order_by(MemoryFact.id)).scalars())


def pending(s: Session) -> list[MemoryFact]:
    return list(s.execute(select(MemoryFact).where(MemoryFact.status == "pending").order_by(MemoryFact.id)).scalars())


def render_card(facts: list[MemoryFact]) -> str:
    if not facts:
        return "（还没有记住关于你的事）"
    lines = []
    for f in facts:
        label = ATTRIBUTES[f.attribute].label + (f"（{f.qualifier}）" if f.qualifier else "")
        when = f"，{f.valid_from.isoformat()} 起" if f.valid_from else ""
        lines.append(f"- {label}：{f.value}{when}  [M-{f.id}]")
    return "\n".join(lines)
