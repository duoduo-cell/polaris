"""把已查到并被正式回答引用的实体转成可持久化的结果卡片。"""
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from polaris.db.models import InterviewQuestion, Job, Post, RawItem, ToolCall
from polaris.followups import get as followup
from polaris.interviews import MATERIAL_LABELS
from polaris.tools.jobs import _job_row, _post_row, job_sources
from polaris.tools.knowledge import question_row

REF_RE = re.compile(r"(?<![A-Za-z0-9])([JPQR]-\d+)(?!\d)")
POST_KINDS = {"面经": "interview", "内推": "referral", "招聘": "recruitment", "经验": "study"}


def refs_from_output(output: str) -> set[str]:
    try:
        value = json.loads(output)
    except (ValueError, TypeError):
        # 兼容旧版截到半途的 JSON；仅接受真实结构的 id/from 字段，不扫描外部原文。
        return set(re.findall(r'(?<!\\)"(?:id|from)"\s*:\s*"([JPQ]-\d+)"', output or "")) if (output or "").startswith("{") else set()
    refs: set[str] = set()
    def walk(node):
        if isinstance(node, dict):
            for k in ("id", "from", "new_version"):
                if isinstance(node.get(k), str) and REF_RE.fullmatch(node[k]):
                    refs.add(node[k])
            for k in ("items", "groups"):
                for item in node.get(k, []) if isinstance(node.get(k), list) else []:
                    walk(item)
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


def presentation(s: Session, answer: str, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = set().union(*(refs_from_output(c.get("output", "")) for c in calls if c.get("ok")))
    selected = list(dict.fromkeys(r for r in REF_RE.findall(answer) if r in allowed))[:12]
    return entities(s, selected)


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
