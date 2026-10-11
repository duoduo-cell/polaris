"""字段冲突裁决：同一个岗位的几条 posting 说法不一致时取哪个值。北极星自己实现（`TECH.md` 第 3 节，环节 3）。

做法（真值发现的最简形式：带权投票）：
1. 每条 posting 按来源有一个可信度：公司官网和招聘系统最高（雇主自己写的），Boss 次之，帖子、转载最低。
2. 同一字段把各条 posting 的值归一后分组；每组的得分 = 组内可信度之和。
3. 得分最高的组胜出；平分时取最近一次采到的那条，再平分取可信度高的。
4. 没写的（空值）不参与投票：只有一条写了就用它，规则记为"只有一处写了"。
5. 有两个以上不同的值就记一条冲突，落败的值和出处都保留（field_sources），不丢。
城市不算冲突：同一岗位多城市挂牌，合并后取并集。
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from polaris.align.match import PostingView

SOURCE_TRUST = {
    "tencent:careers": 1.0, "bytedance:campus": 1.0, "xhs:careers": 1.0,  # 大厂官网：雇主自己写的
    "synthetic:official": 1.0, "synthetic:ats": 0.9,
    "boss": 0.8,
    "synthetic:article": 0.6, "synthetic:forum": 0.5, "xhs": 0.5, "maimai": 0.5,
    "synthetic:group-forward": 0.4,  # 群里转发，最容易走样
}
DEFAULT_TRUST = 0.5
RESOLVED_FIELDS = ("title", "salary_text", "salary_min", "salary_max", "salary_unit", "job_type", "can_convert",
                   "posted_at", "deadline", "start_text", "duration_months", "days_per_week")


def trust(source: str) -> float:
    return SOURCE_TRUST.get(source, DEFAULT_TRUST)


def _norm(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime | date):
        return v.isoformat()[:10]
    if isinstance(v, str):
        return v.strip()
    return v


@dataclass
class Resolution:
    value: Any
    rule: str
    sources: list[int]  # 支持胜出值的 posting
    alternatives: list[dict[str, Any]]  # 落败的值：{value, postings, sources}

    @property
    def conflict(self) -> bool:
        return bool(self.alternatives)


def _earliest(group: list[PostingView], field_name: str) -> Resolution | None:
    dated = sorted((_norm(v.fields.get(field_name)), v.id) for v in group if v.fields.get(field_name))
    if not dated:
        return None
    first = dated[0][0]
    raw = next(v.fields[field_name] for v in group if _norm(v.fields.get(field_name)) == first)
    return Resolution(value=raw, rule="多条挂牌取最早发布", sources=[pid for d, pid in dated if d == first], alternatives=[])


def resolve_field(group: list[PostingView], field_name: str) -> Resolution | None:
    if field_name == "posted_at":  # 同一岗位多次挂牌，发布日期本来就不同，不是矛盾
        return _earliest(group, field_name)
    votes: dict[Any, list[PostingView]] = defaultdict(list)
    raw: dict[Any, Any] = {}
    for v in group:
        value = v.fields.get(field_name)
        if value is None or value == "":
            continue
        k = _norm(value)
        votes[k].append(v)
        raw.setdefault(k, value)
    if not votes:
        return None

    def score(item: tuple[Any, list[PostingView]]) -> tuple[float, Any, float]:
        _, vs = item
        latest = max((v.last_seen or datetime.min for v in vs), default=datetime.min)
        return (sum(trust(v.source) for v in vs), latest, max(trust(v.source) for v in vs))

    ranked = sorted(votes.items(), key=score, reverse=True)
    (win_k, win_vs), losers = ranked[0], ranked[1:]
    if not losers:
        rule = "只有一处写了" if len(win_vs) == 1 else "各处一致"
    elif score(ranked[0])[0] > score(ranked[1])[0]:
        rule = "可信度加权投票"
    else:
        rule = "得分相同，取最近采到的"
    return Resolution(value=raw[win_k], rule=rule, sources=sorted(v.id for v in win_vs),
                      alternatives=[{"value": _norm(raw[k]), "postings": sorted(v.id for v in vs),
                                     "sources": sorted({v.source for v in vs})} for k, vs in losers])


def resolve(group: list[PostingView]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """返回（裁决后的字段值，每个字段的出处，冲突列表）。"""
    values: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    for name in RESOLVED_FIELDS:
        r = resolve_field(group, name)
        if r is None:
            continue
        values[name] = r.value
        sources[name] = {"value": _norm(r.value), "rule": r.rule, "postings": r.sources, "alternatives": r.alternatives}
        if r.conflict and name != "title":  # 岗位名在组内的写法差异（空格、大小写）不算冲突
            conflicts.append({"field": name, "chosen": _norm(r.value), "rule": r.rule, "alternatives": r.alternatives})
    all_cities = sorted(set().union(*(v.cities for v in group)))
    values["city"] = "、".join(all_cities) or None
    sources["city"] = {"value": values["city"], "rule": "多城市取并集", "postings": sorted(v.id for v in group if v.cities),
                       "alternatives": []}
    return values, sources, conflicts
