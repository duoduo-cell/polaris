"""把一次工具调用说成人话，给网页的"思考过程"用：查了什么（发起时）、查到多少（返回后）。

只描述参数和返回的条数，不复述外部原文；编号（J-12、P-3）换成名字，查不到名字就不写。
"""
from typing import Any

from polaris.memory.schema import ATTRIBUTES

JOB_TYPES = {"intern": "实习", "campus": "校招", "social": "社招"}
MATERIALS = {"firsthand": "面经", "study": "备考整理", "uncertain": "待核实资料"}
OF = {"jobs": "岗位", "questions": "面试题", "posts": "帖子"}
GROUP = {"company": "公司", "skill": "技能", "unit": "部门", "city": "城市", "direction": "方向"}
VERBS = {"jobs_search": "搜岗位", "jobs_get": "看岗位详情", "posts_search": "搜帖子", "posts_get": "看帖子",
         "questions_search": "搜面试题", "kg_stats": "查知识图谱", "memory_card": "看你的记忆",
         "memory_history": "查记忆的历史", "memory_known_at": "查当时记着什么", "memory_forget": "忘掉一条记忆",
         "source_open": "打开原链接", "platform_search": "现搜", "jobs_trend": "看招聘变化"}
PLATFORMS = {"xhs": "小红书", "maimai": "脉脉", "boss": "Boss直聘", "tencent": "腾讯招聘官网"}


def _names(refs: list[str]) -> dict[str, str]:
    """J-/P- 编号 → 标题。查库失败不影响描述，只是不带名字。"""
    from sqlalchemy import select

    from polaris.db.models import Job, Post
    from polaris.db.session import session_scope

    ids = {p: [int(r[2:]) for r in refs if r.startswith(f"{p}-") and r[2:].isdigit()] for p in "JP"}
    out: dict[str, str] = {}
    try:
        with session_scope() as s:
            if ids["J"]:
                for j in s.scalars(select(Job).where(Job.id.in_(ids["J"]))):
                    out[f"J-{j.id}"] = " · ".join(x for x in (j.company or j.company_name, j.title) if x)
            if ids["P"]:
                for p in s.scalars(select(Post).where(Post.id.in_(ids["P"]))):
                    if p.summary:
                        out[f"P-{p.id}"] = p.summary[:30]
    except Exception:  # noqa: BLE001
        return {}
    return out


def describe(tool: str, args: dict[str, Any]) -> str:
    """发起时：比如 搜岗位 · 北京 · 实习 · “Agent 开发”。"""
    a = {k: v for k, v in (args or {}).items() if v not in (None, "", [])}
    parts: list[str] = []
    if tool == "jobs_search":
        parts = [a.get("company"), a.get("city"), JOB_TYPES.get(a.get("job_type", "")), a.get("direction"),
                 f"近 {a['since_days']} 天" if a.get("since_days") else None, f"“{a['query']}”" if a.get("query") else None]
    elif tool == "jobs_trend":
        parts = [f"近 {a.get('days', 14)} 天", a.get("company"), a.get("direction"), JOB_TYPES.get(a.get("job_type", ""))]
    elif tool == "platform_search":
        parts = [PLATFORMS.get(a.get("platform", ""), a.get("platform")), a.get("city"), f"“{a['query']}”" if a.get("query") else None]
    elif tool in ("jobs_get", "posts_get", "source_open"):
        ref = a.get("job_id") or a.get("post_id") or a.get("ref") or ""
        name = _names([ref]).get(ref)
        return f"{VERBS[tool]}「{name}」" if name else VERBS[tool]
    elif tool == "posts_search":
        parts = [a.get("company"), a.get("post_type"), MATERIALS.get(a.get("material", "")), a.get("role"),
                 "能内推的" if a.get("has_referral") else None, f"“{a.get('query') or a.get('keyword')}”"
                 if a.get("query") or a.get("keyword") else None]
    elif tool == "questions_search":
        parts = [a.get("company"), a.get("role"), a.get("round"), MATERIALS.get(a.get("material", "")),
                 f"“{a['query']}”" if a.get("query") else None]
    elif tool == "kg_stats":
        filters = [a.get("company"), a.get("skill"), a.get("direction"), a.get("city")]
        parts = [f"数{OF.get(a.get('of', 'jobs'), '岗位')}，按{GROUP.get(a.get('group_by', ''), '类别')}分", *filters]
    elif tool in ("memory_history", "memory_forget"):
        attr = ATTRIBUTES.get(a.get("attribute", ""))
        parts = [attr.label if attr else None, a.get("qualifier"), a.get("value")]
    elif tool == "memory_known_at":
        parts = [str(a["day"]) if a.get("day") else None]
    shown = [str(p) for p in parts if p]
    return " · ".join([VERBS.get(tool, "处理资料"), *shown])


def outcome(tool: str, data: Any, ok: bool, output: str = "") -> str:
    """返回后：找到几条、或者出了什么错（只说类别，不贴原文）。"""
    if not ok:
        first = (output or "").strip().splitlines()[0] if output else ""
        return first.removeprefix("出错了：")[:60] or "没查成"
    if not isinstance(data, dict):
        return "完成"
    if tool == "jobs_trend":
        return f"新出现 {data.get('new', 0)} 个、下架 {data.get('gone', 0)} 个（每日快照 {data.get('snapshot_days', 0)} 天）"
    if tool == "source_open":
        if data.get("online") is False:
            return "页面已经没了（下线或删除）"
        return "和库里存的有变化" if data.get("changed") else "还在，和库里存的一致"
    if tool == "platform_search":
        n = data.get("returned") or 0
        more = len(data.get("also_listed") or [])
        return (f"带回 {n} 条" + (f"，另有 {more} 条只看了标题" if more else "")) if n or more else "平台上没搜到"
    if tool == "kg_stats":
        return f"符合条件的有 {data.get('matched', 0)} 个，分成 {len(data.get('groups') or [])} 组"
    if tool == "memory_card":
        n = len(data.get("facts") or [])
        return f"记着 {n} 条" if n else "还没记住什么"
    if tool in ("memory_history", "memory_known_at"):
        n = len(data.get("versions") or data.get("facts") or [])
        return f"{n} 条记录" if n else "没有记录"
    if "returned" in data:
        n = data.get("returned") or 0
        unit = {"jobs_search": "个岗位", "posts_search": "篇帖子", "questions_search": "道题"}.get(tool, "条")
        more = "，还有更多" if data.get("next_offset") is not None else ""
        return f"找到 {n} {unit}{more}" if n else "没找到符合条件的"
    return "读到了"
