"""跟进卡的态势报告（docs/design/WATCH.md 第 5 节）。

先用规则算出这期的全部事实：发生了什么、范围现状、硬门槛把你挡在哪、技能需求和你的卡片、同类样本、你的投递进展、来源覆盖。
再让模型只根据这些事实写“发生了什么 / 对你意味着什么 / 你还差什么 / 这两天做什么”。
写完校验：报告里出现的编号必须都在事实里；模型失败、超出额度或引用了事实外的编号，就用规则生成的版本，报告不断档。
"""
import json
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from polaris.db.models import Job, Watch, WatchEvent, WatchReport, WatchTarget
from polaris.report import BEIJING
from polaris.watch import KIND_LABELS, coverage, describe, label_of, scope_query

PROMPT_VERSION = "watch-report-v3"  # v3：不写 JSON 字段名、编号跟在名字后；v2：没有来源缺口就不提覆盖、不下“机会渺茫”这类结论
DAILY_LLM_REPORTS = 6  # 每张卡每天最多用模型写几份；超过用规则版本
SCOPE_LIMIT = 300  # 统计硬门槛和技能时最多看多少个范围内岗位
REF = re.compile(r"(?<![A-Za-z0-9])[JPQRM]-\d+(?!\d)")

SYSTEM = """你是北极星，正在给用户写一张求职跟进卡的态势报告。只能使用下面 JSON 里的事实：
- 数字只能照抄，不能自己算新的百分比或推测数量；样本少（少于 5 个）要直说样本少。
- 提到具体岗位、帖子、样本时，先写名字，再在后面括号里写编号，如“字节 · Agent 实习（J-12）”；只能用 JSON 里出现过的编号，不单独罗列编号。
- 不要写出 JSON 的字段名（share、jobs、hard_gates 之类）和引号，用自然的中文说。
- “没写”“未知”不等于“没有”“不会”；同类样本是网上自述，不代表录用标准。
- coverage_gaps 里有来源没查成时，要提醒这期判断不完整，不能说“没有变化”；coverage_gaps 为空就不要提来源覆盖。
- 只说事实和可做的准备，不下“机会渺茫”“没希望”“稳了”这类结论，不估计录用概率。
用中文，分四个小标题：## 发生了什么、## 对你意味着什么、## 你还差什么、## 这两天建议做什么。总共不超过 600 字。只输出报告正文。"""


def _period_events(s: Session, w: Watch, start: datetime, end: datetime) -> list[WatchEvent]:
    return s.scalars(select(WatchEvent).where(WatchEvent.watch_id == w.id, WatchEvent.created_at > start,
                                              WatchEvent.created_at <= end).order_by(WatchEvent.id)).all()


def _scope_facts(s: Session, w: Watch) -> dict[str, Any]:
    from polaris.career_review import fit_summary

    jobs = s.scalars(scope_query(w.criteria).where(Job.status == "active")
                     .order_by(Job.first_seen_at.desc().nulls_last(), Job.id).limit(SCOPE_LIMIT)).all()
    fit = fit_summary(s, list(jobs), w.criteria)
    return {"active_jobs": len(jobs), "capped": len(jobs) >= SCOPE_LIMIT,
            "by_company": dict(Counter(j.company or j.company_name or "未知" for j in jobs).most_common(8)),
            "no_known_conflict": fit["no_known_conflict"], "hard_gates": fit["hard_gates"],
            "unknown_checks": fit["unknown_checks"],
            "skills": [{**x, "share": f"{round(x['share'] * 100)}%" if x["share"] is not None else None} for x in fit["skills"][:10]],
            "examples": [{"ref": f"J-{j.id}", "label": label_of(j)} for j in jobs[:5]]}


def _peer_facts(w: Watch) -> dict[str, Any] | None:
    from polaris.tools.peers import PeersSearchArgs, peers_search

    if not w.criteria.get("direction"):
        return None
    job_type = {"intern": "实习", "campus": "校招", "social": "社招"}.get(w.criteria.get("job_type") or "")
    out = peers_search(PeersSearchArgs(direction=w.criteria["direction"], job_type=job_type,
                                       big_only=bool(w.criteria.get("big_only")), since_days=180, limit=5))
    summary = out["summary"]
    return {"scope": "近半年同方向" + (f"、{job_type}" if job_type else "") + "的自述样本", **summary,
            "examples": [{"ref": r["id"], "stage": r["stage"], "school": r["school"], "outcome": r["outcome"],
                          "reached": r["reached"], "published": r["published"]} for r in out["items"]]}


def facts(s: Session, w: Watch, start: datetime, end: datetime) -> dict[str, Any]:
    from polaris.followups import funnel
    from polaris.memory.update import card

    events = _period_events(s, w, start, end)
    shown = [e for e in events if e.importance != "quiet"]
    targets = s.scalars(select(WatchTarget).where(WatchTarget.watch_id == w.id).order_by(WatchTarget.id)).all()
    me = {f.attribute: f.value for f in card(s, end.astimezone(BEIJING).date())
          if f.attribute in ("degree", "school", "grad_year", "days_per_week", "duration_months")}
    skills = sorted({f.value for f in card(s, end.astimezone(BEIJING).date()) if f.attribute == "skill"})
    progress = funnel(s)
    return {
        "watch": {"title": w.title, "scope": describe(w.criteria), "until": w.until.isoformat() if w.until else None},
        "period": {"from": start.astimezone(BEIJING).strftime("%m-%d %H:%M"), "to": end.astimezone(BEIJING).strftime("%m-%d %H:%M")},
        "events": [{"importance": e.importance, "kind": KIND_LABELS.get(e.kind, e.kind), "title": e.title, "ref": e.ref}
                   for e in shown[:30]],
        "event_counts": dict(Counter(e.importance for e in events)),
        "targets": [{"ref": f"J-{t.job_id}" if t.job_id else None, "label": t.label, "status": (t.snapshot or {}).get("status"),
                     "days_per_week": (t.snapshot or {}).get("days_per_week")} for t in targets],
        "scope": _scope_facts(s, w),
        "peers": _peer_facts(w),
        "you": {"card": me, "skills": skills, "progress": {x["label"]: x["count"] for x in progress["steps"]},
                "rejection_feedback": progress["rejections"][:5]},
        "coverage_gaps": coverage(s, end),
    }


def allowed_refs(f: dict[str, Any]) -> set[str]:
    return set(REF.findall(json.dumps(f, ensure_ascii=False)))


def rule_markdown(f: dict[str, Any]) -> str:
    """不调模型的版本：模型失败、超额度或写出事实外编号时用它。"""
    lines = ["## 发生了什么", ""]
    if f["events"]:
        for e in f["events"]:
            mark = "【紧急】" if e["importance"] == "urgent" else ""
            lines.append(f"- {mark}{e['title']}" + (f"（{e['ref']}）" if e["ref"] else ""))
    else:
        lines.append("- 这期没有新的变化。" if not f["coverage_gaps"] else "- 没发现变化，但有来源没查成，见下面。")
    sc = f["scope"]
    lines += ["", "## 对你意味着什么", "",
              f"- 范围（{f['watch']['scope']}）内现在在招 {sc['active_jobs']} 个岗位"
              + ("（只统计了前 300 个）" if sc["capped"] else "") + f"，其中没有已知硬冲突的 {sc['no_known_conflict']} 个。"]
    if sc["hard_gates"]:
        lines.append("- 把你挡在外面的硬门槛：" + "，".join(f"{k} {v} 个" for k, v in sc["hard_gates"].items()) + "。")
    if sc["unknown_checks"]:
        lines.append("- 岗位没写清、需要问 HR 的：" + "，".join(f"{k} {v} 个" for k, v in sc["unknown_checks"].items()) + "。")
    states = {"active": "在招", "gone": "已下架"}
    for t in f["targets"]:
        lines.append(f"- 重点岗位 {t['label']}" + (f"（{t['ref']}）" if t["ref"] else "") + f"：{states.get(t['status'], '状态未知')}。")
    lines += ["", "## 你还差什么", ""]
    missing = [x for x in sc["skills"] if not x["in_your_card"]][:5]
    if missing:
        lines.append("- 范围内岗位常要求、你卡片里没写的技能：" + "，".join(f"{x['skill']}（{x['jobs']} 个岗位）" for x in missing)
                     + "。没写不等于不会，会的话补进卡片。")
    p = f["peers"]
    if p:
        outcome = p["by_outcome"]
        lines.append(f"- {p['scope']} {p['matched']} 人：拿到 offer {outcome.get('拿到offer', 0)}、挂了 {outcome.get('挂了', 0)}；"
                     f"写了论文的 {p['stated_papers']} 人，写了实习的 {p['stated_internships']} 人。"
                     + ("样本太少，只能参考。" if p["matched"] < 5 else ""))
    for r in f["you"]["rejection_feedback"]:
        lines.append(f"- 你记下的被拒反馈：{r['for'] or ''} {r['reason']}")
    if len(lines) and lines[-1] == "":
        lines.append("- 卡片和样本里暂时看不出明显差距。")
    lines += ["", "## 这两天建议做什么", ""]
    urgent = [e for e in f["events"] if e["importance"] == "urgent"]
    if urgent:
        lines.append("- 先处理上面标【紧急】的变化：核对原文，必要时问 HR。")
    if missing:
        lines.append(f"- 准备 {missing[0]['skill']} 相关的项目说明或自测题。")
    if f["coverage_gaps"]:
        lines.append("- 有来源没查成：" + "；".join(f"{g['label']}：{g['problem']}" for g in f["coverage_gaps"]) + "。")
    if lines[-1] == "":
        lines.append("- 保持关注，没有需要马上做的事。")
    return "\n".join(lines)


def write_report(s: Session, w: Watch, now: datetime | None = None, *, llm: Any = None,
                 use_model: bool = True) -> WatchReport:
    from polaris.llm.client import LLM

    now = now or datetime.now(UTC)
    start = w.last_report_at or w.baseline_at
    f = facts(s, w, start, now)
    markdown, fallback, call_id = rule_markdown(f), None, None
    today = now.astimezone(BEIJING).replace(hour=0, minute=0, second=0, microsecond=0)
    used = len(s.scalars(select(WatchReport.id).where(WatchReport.watch_id == w.id, WatchReport.created_at >= today,
                                                      WatchReport.fallback.is_(None))).all())
    if not use_model:
        fallback = "rule_only"
    elif used >= DAILY_LLM_REPORTS:
        fallback = "daily_model_budget"
    else:
        try:
            out = (llm or LLM()).chat([{"role": "system", "content": SYSTEM},
                                       {"role": "user", "content": json.dumps(f, ensure_ascii=False)}],
                                      purpose="watch_report", prompt_version=PROMPT_VERSION, temperature=0.2)
            text, call_id = (out.content or "").strip(), out.call_id
            stray = set(REF.findall(text)) - allowed_refs(f)
            if not text:
                fallback = "empty_model_output"
            elif stray:
                fallback = "refs_outside_facts:" + ",".join(sorted(stray))[:40]
            else:
                markdown = text
        except Exception as e:  # noqa: BLE001  模型挂了也要有报告
            fallback = f"model_error:{type(e).__name__}"[:64]
    report = WatchReport(watch_id=w.id, period_start=start, period_end=now, facts=f, markdown=markdown,
                         fallback=fallback, llm_call_id=call_id, created_at=now)
    s.add(report)
    w.last_report_at = now
    s.flush()
    return report
