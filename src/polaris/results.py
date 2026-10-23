"""把已查到并被正式回答引用的实体转成可持久化的结果卡片。"""
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from polaris.db.models import ChatMessage, InterviewQuestion, Job, Post, RawItem, ToolCall
from polaris.followups import get as followup
from polaris.interviews import MATERIAL_LABELS
from polaris.tools.jobs import _job_row, _post_row, job_sources
from polaris.tools.knowledge import question_row

REF_RE = re.compile(r"(?<![A-Za-z0-9])([JPQR]-\d+)(?!\d)")
POST_KINDS = {"面经": "interview", "内推": "referral", "招聘": "recruitment", "经验": "study"}


def unverified_citations(s: Session, answer: str, calls: list[dict[str, Any]], thread_id: str) -> set[str]:
    """检查明确括注的实体引用；不是自然语言事实正确性的完整审查。"""
    prose = re.sub(r"```[\s\S]*?```|`[^`\n]*`", "", answer)
    cited = set(re.findall(r"[\[【（(]\s*([JPQR]-\d+)\s*[\]】）)]", prose))
    if not cited:
        return set()
    supported = set().union(*(refs_from_output(c.get("output", "")) for c in calls if c.get("ok")))
    if missing := cited - supported:
        # 已有正式结果可在同一对话继续引用，不要求重复工具调用来取同一份证据。
        prior = s.scalars(select(ChatMessage).where(ChatMessage.thread_id == thread_id, ChatMessage.role == "assistant")
                          .order_by(ChatMessage.id.desc()).limit(20)).all()
        supported.update(r.get("id") for message in prior for r in (message.turn or {}).get("results", [])
                         if isinstance(r, dict) and r.get("id") in missing)
    return cited - supported


# 工具输出里放编号的字段：整个字段就是一个编号才算（kg_stats 的例子、jobs_trend 的新岗位、来源的原始记录、source_open 的对象）。
# 不扫描外部原文里的文字，帖子正文写个 "J-1" 不会变成卡片。
REF_KEYS = ("id", "from", "new_version", "raw_id", "ref")
REF_LISTS = ("examples", "examples_new")


def refs_from_output(output: str) -> set[str]:
    try:
        value = json.loads(output)
    except (ValueError, TypeError):
        # 兼容旧版截到半途的 JSON；仅接受真实结构的字段，不扫描外部原文。
        keys = "|".join(REF_KEYS)
        return set(re.findall(rf'(?<!\\)"(?:{keys})"\s*:\s*"([JPQR]-\d+)"', output or "")) if (output or "").startswith("{") else set()
    refs: set[str] = set()
    def walk(node):
        if isinstance(node, dict):
            for k in REF_KEYS:
                if isinstance(node.get(k), str) and REF_RE.fullmatch(node[k]):
                    refs.add(node[k])
            for k in REF_LISTS:
                if isinstance(node.get(k), list):
                    refs.update(x for x in node[k] if isinstance(x, str) and REF_RE.fullmatch(x))
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(value)
    return refs


def entities(s: Session, references: list[str], *, detailed: bool = False) -> list[dict[str, Any]]:
    ids = {prefix: [int(r[2:]) for r in references if r.startswith(f"{prefix}-") and REF_RE.fullmatch(r)] for prefix in "JPQR"}
    found: dict[str, dict[str, Any]] = {}
    if ids["R"]:
        from polaris.live import raw_card

        for raw in s.scalars(select(RawItem).where(RawItem.id.in_(ids["R"]))).all():
            card = raw_card(raw)
            if not detailed:
                card.pop("text", None)
            found[card["id"]] = card
    jobs = s.scalars(select(Job).where(Job.id.in_(ids["J"]))).all()
    sources = job_sources(s, [j.id for j in jobs])
    from polaris.match import matches
    fits = matches(s, [j.id for j in jobs]) if jobs else {}  # 和你的条件对得上多少，只给界面看
    for j in jobs:
        data = {**_job_row(j, True), "kind": "job", "sources": sources[j.id], "status": j.status,
                "requirements": j.requirements or [], "directions": j.directions or [],
                "conflicts": j.conflicts or [], "followup": followup(s, j.id, history=detailed)}
        if j.id in fits:
            data["match"] = fits[j.id]
        if detailed:
            data.update(jd_text=j.jd_text or "", field_sources=j.field_sources or {})
        found[data["id"]] = data
    posts = s.execute(select(Post, RawItem).join(RawItem, RawItem.id == Post.raw_item_id).where(Post.id.in_(ids["P"]))).all()
    for p, r in posts:
        data = _post_row(p, r, True)
        data["kind"] = POST_KINDS.get(p.post_type, "post")
        if data["kind"] == "interview" and data["material"] != "firsthand":
            data["kind"] = "study" if data["material"] == "study" else "post"
        if detailed:
            from polaris.interviews import profile, split_text
            data.update(text=split_text(r.raw_text, r.raw_json)[1], advice=p.advice or [],
                        question_details=[{**q, "interview_date": q["interview_date"].isoformat() if q["interview_date"] else None,
                                           "published_at": q["published_at"].isoformat() if q["published_at"] else None,
                                           "material_label": MATERIAL_LABELS[q["material"]]}
                                          for q in profile(p, r.raw_text, r.raw_json)["questions"]])
        found[data["id"]] = data
    questions = s.execute(select(InterviewQuestion, Post, RawItem).join(Post, Post.id == InterviewQuestion.post_id)
                          .join(RawItem, RawItem.id == Post.raw_item_id).where(InterviewQuestion.id.in_(ids["Q"]))).all()
    for q, p, r in questions:
        data = {**question_row(q, p, r), "kind": "question"}
        found[data["id"]] = data
    return [found[r] for r in references if r in found]


def titles(s: Session, references: list[str]) -> dict[str, str]:
    """编号 → 给人看的名字（只查名字，不带来源和跟进），图谱的例子等地方用。"""
    ids = {prefix: [int(r[2:]) for r in references if r.startswith(f"{prefix}-") and REF_RE.fullmatch(r)] for prefix in "JPQ"}
    out: dict[str, str] = {}
    for j in s.scalars(select(Job).where(Job.id.in_(ids["J"]))).all() if ids["J"] else []:
        out[f"J-{j.id}"] = " · ".join(x for x in (j.company or j.company_name, j.title) if x)
    for p in s.scalars(select(Post).where(Post.id.in_(ids["P"]))).all() if ids["P"] else []:
        out[f"P-{p.id}"] = (p.summary or "帖子")[:40]
    for q in s.scalars(select(InterviewQuestion).where(InterviewQuestion.id.in_(ids["Q"]))).all() if ids["Q"] else []:
        out[f"Q-{q.id}"] = q.question[:40]
    return out


def _reference_only_jobs(answer: str) -> set[str]:
    """仅识别独立段落开头的明确未列出声明；保守处理混合／后续改为推荐的表述。

    这是卡片布局规则，不是通用推荐意图分类或资格判断。证据和行内详情入口始终保留。
    """
    mentions: dict[str, list[bool]] = {}
    for paragraph in re.split(r"\n\s*\n", answer):
        refs = {r for r in REF_RE.findall(paragraph) if r.startswith("J-")}
        plain = paragraph.lstrip(" \t>*-#")
        explicit = (len(refs) == 1 and not re.search(r"[?？]", plain)
                    and bool(re.match(r"未列出|未放入|不推荐|不纳入|仅作对照|仅供对照", plain)))
        for ref in refs:
            mentions.setdefault(ref, []).append(explicit)
    return {ref for ref, flags in mentions.items() if all(flags)}


def presentation(s: Session, answer: str, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = set().union(*(refs_from_output(c.get("output", "")) for c in calls if c.get("ok")))
    selected = list(dict.fromkeys(r for r in REF_RE.findall(answer) if r in allowed))[:12]
    cards = entities(s, selected)
    # 正式卡片保留该轮已成功查询时的条件对照，不能重新按长期画像覆盖它。
    snapshots = {}
    for call in calls:
        if not call.get("ok") or call.get("tool") not in ("jobs_search", "jobs_get"):
            continue
        try:
            data = json.loads(call.get("output", ""))
        except (TypeError, ValueError):
            continue
        for item in data.get("items", [data]) if isinstance(data, dict) else []:
            if isinstance(item, dict) and item.get("id") in selected and isinstance(item.get("match"), dict):
                snapshots[item["id"]] = item["match"]
    reference_only = _reference_only_jobs(answer)
    return [{**card, **({"match": snapshots[card["id"]]} if card["id"] in snapshots else {}),
             **({"show_card": False} if card["id"] in reference_only else {})} for card in cards]


def activity(call: dict[str, Any], data: Any = None) -> dict[str, Any]:
    if data is None:
        try:
            data = json.loads(call.get("output", ""))
        except (ValueError, TypeError):
            data = {}
    items = data.get("items", []) if isinstance(data, dict) else []
    if isinstance(data, dict) and data.get("sources"):
        items = [data]
    labels = list(dict.fromkeys(src.get("label", src.get("source", "")) for item in items if isinstance(item, dict)
                                for src in item.get("sources", []) if isinstance(src, dict)))
    out = {k: call.get(k) for k in ("tool", "args", "ok", "latency_ms", "error_kind")}
    out.update({"returned": data.get("returned") if isinstance(data, dict) else None, "sources": labels})
    if not call.get("ok"):
        out["error"] = call.get("output", "工具调用失败")[:300]
    return out


def enrich_history(s: Session, message: Any) -> dict[str, Any] | None:
    turn = message.turn or {}
    if "results" in turn or not REF_RE.search(message.content):
        return message.turn
    calls = s.scalars(select(ToolCall).where(ToolCall.thread_id == message.thread_id, ToolCall.ok.is_(True),
                                            ToolCall.created_at <= message.created_at)
                       .order_by(ToolCall.id.desc()).limit(500)).all()
    cards = presentation(s, message.content, [{"ok": c.ok, "output": c.output} for c in calls])
    out = {"answer": message.content, "memory_events": [], "memory_questions": [], "approvals": [],
           "tool_calls": [], "latency_ms": 0, "error": None, **turn, "results": cards}
    # 只保存展示数据，不修改原话或重新执行模型。
    message.turn = out
    return out
