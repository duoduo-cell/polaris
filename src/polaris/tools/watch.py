"""跟进卡的两个工具（docs/design/WATCH.md 第 7 节）：

- watch_status：看现有跟进卡、最近的变化和最新态势报告（只读）。
- watch_create：开一张跟进卡。它会建一个长期运行的委托，所以和 memory_forget 一样要用户在网页上批准后才执行。
"""
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from polaris.db.models import Watch
from polaris.db.session import session_scope
from polaris.tools.base import ToolError, ToolSpec


class WatchStatusArgs(BaseModel):
    watch_id: int | None = Field(None, description="只看某一张；不填看全部（结束的不列）")


def watch_status(args: WatchStatusArgs) -> dict:
    from polaris.watch import view

    with session_scope() as s:
        stmt = select(Watch).order_by(Watch.id.desc())
        stmt = stmt.where(Watch.id == args.watch_id) if args.watch_id else stmt.where(Watch.status != "ended")
        rows = s.scalars(stmt.limit(10)).all()
        if args.watch_id and not rows:
            raise ToolError(f"没有第 {args.watch_id} 张跟进卡", "不填 watch_id 看全部", "{}")
        out = []
        for w in rows:
            v = view(s, w, events=15)
            v["events"] = [e for e in v["events"] if e["importance"] != "quiet"][:10]
            if v["report"]:
                v["report"]["markdown"] = v["report"]["markdown"][:1500]
            for t in v["targets"]:
                t.pop("snapshot", None)
            out.append(v)
    note = ("跟进卡由常驻采集按间隔检查本地库的变化（不额外加大平台采集频率）；紧急的发系统通知，其余进每天的态势报告。"
            "事件里的编号可以直接引用。来源没查成（source_gap）时，不能说“没有变化”。")
    if not out:
        return {"watches": [], "note": "还没有跟进卡。用户要“持续盯着”时可以用 watch_create 提议开一张。"}
    return {"watches": out, "note": note}


class WatchCreateArgs(BaseModel):
    title: str = Field(..., max_length=60, description="一句话的名字，如：北京 Agent 日常实习")
    city: str | None = Field(None, description="城市，如 北京")
    job_type: Literal["intern", "campus", "social"] | None = Field(None, description="intern 实习、campus 校招、social 社招")
    direction: str | None = Field(None, description="方向，如 Agent、RAG")
    max_days_per_week: int | None = Field(None, ge=1, le=7, description="用户每周最多能去几天")
    big_only: bool = Field(True, description="只看大厂名单里的公司")
    job_ids: list[str] = Field(default_factory=list, max_length=10, description="重点盯的岗位编号，如 [\"J-12\"]，来自本轮查到的岗位")
    days: int = Field(14, ge=1, le=90, description="盯多少天")
    cadence_minutes: int = Field(60, ge=10, le=1440, description="多久检查一次本地库，默认一小时")


def watch_create(args: WatchCreateArgs) -> dict:
    from polaris.tools.jobs import _parse
    from polaris.watch import WatchError, create, view

    ids = [_parse(ref, "J", "jobs_search", '{"job_ids": ["J-12"]}') for ref in args.job_ids]
    criteria = {"city": args.city, "job_type": args.job_type, "direction": args.direction,
                "max_days_per_week": args.max_days_per_week, "big_only": args.big_only}
    until = (datetime.now(UTC) + timedelta(days=args.days)).date()
    try:
        with session_scope() as s:
            w = create(s, title=args.title, criteria=criteria, job_ids=ids, until=until, cadence_minutes=args.cadence_minutes)
            out = view(s, w, events=0)
    except WatchError as e:
        raise ToolError(str(e), "改一下参数再试", '{"title": "北京 Agent 日常实习", "city": "北京", "direction": "Agent"}') from e
    return {"created": out, "note": f"跟进卡已开，盯到 {until.isoformat()}；第一次检查只建基线，之后有变化才会提醒。"
                                    "用户可以在网页“跟进”页暂停、改条件或结束。"}


TOOLS = [
    ToolSpec(name="watch_status",
             description="看用户的跟进卡：盯的范围和重点岗位、最近发现的变化（下架、要求改了、新岗位、新面经、同类样本、来源没查成）"
                         "和最新的态势报告。用户问“我盯的那些岗位怎么样了”“最近有什么变化”“现在形势怎样”时用。",
             args_schema=WatchStatusArgs, func=watch_status, example="{}"),
    ToolSpec(name="watch_create",
             description="开一张跟进卡：用户明确要求“持续关注 / 帮我盯着 / 有变化告诉我”时用。范围用本轮用户说的条件，"
                         "重点岗位只放本轮查到、用户关心的几个。要用户批准后才会建。",
             args_schema=WatchCreateArgs, func=watch_create, read_only=False,
             example='{"title": "北京 Agent 日常实习", "city": "北京", "job_type": "intern", "direction": "Agent", '
                     '"max_days_per_week": 4, "job_ids": ["J-12"], "days": 14}'),
]
