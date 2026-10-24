"""跟进卡：用户委托北极星长期盯着的一个求职目标（设计见 docs/design/WATCH.md）。

一次检查只读本地库、不调模型：重点岗位逐字段比快照，范围内在招岗位比集合（新增 / 下架），再看新面经、新的同类样本和来源覆盖。
发现的每件事记成一条事件，按“类型 + 对象 + 变化内容”去重：重复检查、进程重启后重跑都不会多记。
紧急的发系统通知；其余进每天的态势报告（polaris/watch_report.py）。
"""
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import any_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polaris.config import get_settings
from polaris.data_scope import synthetic_filter
from polaris.db.models import Job, PeerProfile, Post, Run, Watch, WatchEvent, WatchTarget
from polaris.matching_scope import criteria as clean_criteria
from polaris.report import BEIJING

IMPORTANCE = ("urgent", "brief", "quiet")
KIND_LABELS = {
    "job_closed": "重点岗位下架", "job_reopened": "重点岗位重新出现", "job_changed": "重点岗位有改动",
    "job_removed": "重点岗位已不在库里", "new_job": "范围内新岗位", "scope_entered": "岗位进入你的范围",
    "scope_closed": "范围内岗位下架", "new_material": "新面经 / 内推", "new_peer": "新的同类样本", "source_gap": "来源没查成",
}
FIELD_LABELS = {"status": "状态", "title": "岗位名", "city": "城市", "cities": "城市", "job_type": "类型",
                "days_per_week": "每周天数", "duration_months": "实习时长（月）", "deadline": "截止日",
                "requirements": "任职要求", "jd_hash": "JD 正文"}
JOB_TYPE_NAMES = {"intern": "实习", "campus": "校招", "social": "社招"}
OVERLAP = timedelta(hours=6)  # 按时间找新帖子时往前多看一点，晚入库的不漏；重复的由 fingerprint 挡住
MATERIAL_DAYS = 30  # 新面经只提最近一个月发的
PEER_DAYS = 60
OFFICIAL_STALE = timedelta(hours=36)  # 官网每日重采超过这么久没成功，算覆盖缺口
PLATFORM_STALE = timedelta(hours=26)
LOGIN_SOURCES = {"xhs": "小红书", "maimai": "脉脉", "boss": "Boss直聘"}


class WatchError(ValueError):
    pass


def normalize_criteria(raw: dict[str, Any] | None) -> dict[str, Any]:
    """跟进卡的范围：沿用搜索工具的条件口径（城市、类型、方向、每周最多天数），另外记住是否只看大厂和关键词。"""
    raw = raw or {}
    out = clean_criteria(raw)
    out["big_only"] = bool(raw.get("big_only", not get_settings().demo_mode))
    if isinstance(raw.get("query"), str) and raw["query"].strip():
        out["query"] = raw["query"].strip()[:100]
    return out


def describe(criteria: dict[str, Any]) -> str:
    parts = [criteria.get("city"), JOB_TYPE_NAMES.get(criteria.get("job_type") or ""), criteria.get("direction"),
             f"每周最多 {criteria['max_days_per_week']} 天" if criteria.get("max_days_per_week") else None,
             "只看大厂" if criteria.get("big_only") else None, f"关键词“{criteria['query']}”" if criteria.get("query") else None]
    return "、".join(p for p in parts if p) or "不限"


def scope_query(criteria: dict[str, Any]):
    """范围内的岗位（不限状态）；条件口径和 jobs_search 的结构化筛选一致：没写天数的不排除，留给匹配标待确认。"""
    stmt = select(Job).where(synthetic_filter(Job.is_synthetic))
    if criteria.get("city"):
        stmt = stmt.where(Job.city.ilike(f"%{criteria['city'].removesuffix('市')}%"))
    if criteria.get("job_type"):
        stmt = stmt.where(Job.job_type == criteria["job_type"])
    if criteria.get("direction"):
        d = criteria["direction"]
        stmt = stmt.where(or_(any_(Job.directions) == d, Job.title.ilike(f"%{d}%")))
    if criteria.get("big_only"):
        stmt = stmt.where(Job.company_id.is_not(None))
    if criteria.get("max_days_per_week"):
        stmt = stmt.where(or_(Job.days_per_week.is_(None), Job.days_per_week <= criteria["max_days_per_week"]))
    if criteria.get("query"):
        q = f"%{criteria['query']}%"
        stmt = stmt.where(or_(Job.title.ilike(q), Job.jd_text.ilike(q)))
    return stmt


def snapshot(job: Job) -> dict[str, Any]:
    """重点岗位用来比较的字段。JD 正文只记指纹：正文变了但要求条目没变时，只算小改动。"""
    return {"status": job.status, "title": job.title, "city": job.city, "cities": sorted(job.cities or []),
            "job_type": job.job_type, "days_per_week": job.days_per_week, "duration_months": job.duration_months,
            "deadline": job.deadline.isoformat() if job.deadline else None,
            "requirements": list(job.requirements or []),
            "jd_hash": hashlib.md5((job.jd_text or "").encode()).hexdigest()[:12]}


def label_of(job: Job) -> str:
    return " · ".join(x for x in (job.company or job.company_name, job.title) if x)


def _digest(value: Any) -> str:
    return hashlib.md5(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:10]


# ---- 建卡、改卡 ----

def create(s: Session, *, title: str, criteria: dict[str, Any] | None = None, job_ids: list[int] | None = None,
           until: date | None = None, cadence_minutes: int = 60, report_hour: int = 21, notify: str = "desktop",
           now: datetime | None = None) -> Watch:
    now = now or datetime.now(UTC)
    if not title.strip():
        raise WatchError("跟进卡要有个名字")
    if not 10 <= cadence_minutes <= 24 * 60:
        raise WatchError("检查间隔在 10 分钟到 24 小时之间")
    if not 0 <= report_hour <= 23:
        raise WatchError("报告时间是 0 到 23 点")
    if notify not in ("desktop", "brief"):
        raise WatchError("提醒方式只能是 desktop 或 brief")
    w = Watch(title=title.strip()[:200], criteria=normalize_criteria(criteria), until=until, cadence_minutes=cadence_minutes,
              report_hour=report_hour, notify=notify, baseline_at=now, next_check_at=now, state={})
    s.add(w)
    s.flush()
    for job_id in job_ids or []:
        add_target(s, w, job_id, now)
    return w


def add_target(s: Session, w: Watch, job_id: int, now: datetime | None = None) -> WatchTarget:
    job = s.get(Job, job_id)
    if job is None or job.is_synthetic != get_settings().demo_mode:
        raise WatchError(f"没有 J-{job_id} 这个岗位")
    existing = s.scalar(select(WatchTarget).where(WatchTarget.watch_id == w.id, WatchTarget.job_id == job_id))
    if existing:
        return existing
    # 加入时就拍下快照：之后的变化都相对加入那一刻
    t = WatchTarget(watch_id=w.id, job_id=job_id, label=label_of(job), snapshot=snapshot(job), snapshot_at=now or datetime.now(UTC))
    s.add(t)
    s.flush()
    return t


def remove_target(s: Session, w: Watch, job_id: int) -> None:
    t = s.scalar(select(WatchTarget).where(WatchTarget.watch_id == w.id, WatchTarget.job_id == job_id))
    if t:
        s.delete(t)


def update(s: Session, w: Watch, changes: dict[str, Any], now: datetime | None = None) -> Watch:
    """改条件时版本加一、清空范围基线：旧条件下还没发的提醒不再发，新条件从下次检查重新建基线，不把整片范围当成“新增”。"""
    now = now or datetime.now(UTC)
    if "criteria" in changes:
        new = normalize_criteria(changes["criteria"])
        if new != w.criteria:
            w.criteria, w.version, w.state = new, w.version + 1, {}
    if "status" in changes:
        if changes["status"] not in ("active", "paused", "ended"):
            raise WatchError("状态只能是 active、paused、ended")
        if w.status == "ended" and changes["status"] != "ended":
            raise WatchError("结束的跟进卡不能再打开，新建一张")
        if changes["status"] == "active" and w.status == "paused":
            w.next_check_at = now  # 恢复后马上查一次
        w.status = changes["status"]
    for key in ("title", "until", "cadence_minutes", "report_hour", "notify"):
        if key in changes:
            setattr(w, key, changes[key])
    if not (w.title or "").strip():
        raise WatchError("跟进卡要有个名字")
    if not 10 <= w.cadence_minutes <= 24 * 60 or not 0 <= w.report_hour <= 23 or w.notify not in ("desktop", "brief"):
        raise WatchError("检查间隔、报告时间或提醒方式不合法")
    w.updated_at = now
    return w


# ---- 检查 ----

class Recorder:
    """一次检查里收集事件；写库时按 fingerprint 去重。"""

    def __init__(self, s: Session, w: Watch, now: datetime):
        self.s, self.w, self.now, self.added = s, w, now, 0

    def add(self, kind: str, importance: str, fingerprint: str, title: str, ref: str | None = None,
            **detail: Any) -> None:
        stmt = insert(WatchEvent).values(
            watch_id=self.w.id, watch_version=self.w.version, kind=kind, importance=importance,
            fingerprint=fingerprint[:200], ref=ref, title=title[:500], detail=detail, created_at=self.now)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_watch_events_fingerprint").returning(WatchEvent.id)
        self.added += len(self.s.execute(stmt).all())  # rowcount 在 ON CONFLICT 下不可靠（实测返回 -1），按真正插入的行数


def _conflicts_with(criteria: dict[str, Any], field: str, value: Any) -> bool:
    """这个字段的新值是否违反本卡的硬条件。"""
    if field == "days_per_week" and value and criteria.get("max_days_per_week"):
        return value > criteria["max_days_per_week"]
    if field in ("city", "cities") and criteria.get("city"):
        cities = value if isinstance(value, list) else [value]
        return bool(cities) and not any(criteria["city"].removesuffix("市") in (c or "") for c in cities)
    if field == "job_type" and value and criteria.get("job_type"):
        return value != criteria["job_type"]
    return False


def _field_change(field: str, before: Any, after: Any, conflict: bool) -> dict[str, Any]:
    """列表字段（任职要求、城市）只记增删的条目，不把整份列表前后各抄一遍。"""
    out = {"field": field, "label": FIELD_LABELS.get(field, field), "before": before, "after": after, "conflict": conflict}
    if isinstance(before, list) and isinstance(after, list):
        out.update(added=[x for x in after if x not in before], removed=[x for x in before if x not in after])
    return out


def _check_targets(s: Session, w: Watch, rec: Recorder) -> int:
    targets = s.scalars(select(WatchTarget).where(WatchTarget.watch_id == w.id)).all()
    for t in targets:
        job = s.get(Job, t.job_id) if t.job_id is not None else None
        if job is None:
            # 岗位记录被清理时外键把 job_id 置空；用快照上的标记保证只报一次
            if not (t.snapshot or {}).get("removed"):
                rec.add("job_removed", "brief", f"removed:{t.id}", f"{t.label} 已不在库里（不等于下架，可能是合并或清理）",
                        ref=None, label=t.label, last_snapshot=t.snapshot)
                t.snapshot, t.job_id = {**(t.snapshot or {}), "removed": True}, None
            continue
        new, old = snapshot(job), t.snapshot or {}
        ref = f"J-{job.id}"
        if old.get("status") != new["status"] and old.get("status") is not None:
            if new["status"] == "gone":
                rec.add("job_closed", "urgent", f"closed:{job.id}:{_digest(job.last_seen_at)}",
                        f"{label_of(job)} 官网已经连续一天以上没再出现，按下架处理", ref=ref, label=label_of(job),
                        last_seen=job.last_seen_at.isoformat() if job.last_seen_at else None)
            else:
                rec.add("job_reopened", "brief", f"reopened:{job.id}:{_digest(job.last_seen_at)}",
                        f"{label_of(job)} 又出现在官网上了", ref=ref, label=label_of(job))
        changed = [f for f in new if f not in ("status", "jd_hash") and old.get(f) != new[f] and f in old]
        if changed:
            urgent = any(_conflicts_with(w.criteria, f, new[f]) for f in changed)
            fields = [_field_change(f, old.get(f), new[f], _conflicts_with(w.criteria, f, new[f])) for f in changed]
            what = "、".join(dict.fromkeys(FIELD_LABELS.get(f, f) for f in changed))
            rec.add("job_changed", "urgent" if urgent else "brief", f"changed:{job.id}:{_digest({f: new[f] for f in changed})}",
                    f"{label_of(job)} 改了{what}" + ("，和你的条件冲突了" if urgent else ""), ref=ref, fields=fields,
                    label=label_of(job))
        elif old.get("jd_hash") and old.get("jd_hash") != new["jd_hash"]:
            rec.add("job_changed", "quiet", f"jd:{job.id}:{new['jd_hash']}", f"{label_of(job)} 的 JD 正文有小改动，要求条目没变",
                    ref=ref, label=label_of(job),
                    fields=[{"field": "jd_hash", "label": "JD 正文", "before": old.get("jd_hash"), "after": new["jd_hash"]}])
        t.snapshot, t.snapshot_at, t.label = new, rec.now, label_of(job)
    return len(targets)


def _check_scope(s: Session, w: Watch, rec: Recorder) -> dict[str, Any]:
    """范围内在招岗位比上次的集合：多出来的是新增或刚进入范围，少了的看是不是下架。第一次只建基线，不报。"""
    from polaris.match import matches

    active = {j.id: j for j in s.scalars(scope_query(w.criteria).where(Job.status == "active"))}
    previous = w.state.get("scope_active")
    if previous is None:
        w.state = {**w.state, "scope_active": sorted(active)}
        return {"active": len(active), "baseline": True}
    prev = set(previous)
    # 重点岗位的下架、重新出现已经在逐字段比较里单独报过，范围视角不再重复
    targets = set(s.scalars(select(WatchTarget.job_id).where(WatchTarget.watch_id == w.id, WatchTarget.job_id.is_not(None))))
    entered = [j for i, j in active.items() if i not in prev and i not in targets]
    fits = matches(s, [j.id for j in entered], criteria=clean_criteria(w.criteria)) if entered else {}
    for j in entered:
        ref, fit = f"J-{j.id}", fits.get(j.id) or {}
        if j.first_seen_at and j.first_seen_at > w.baseline_at:
            checks = {c["key"]: c["ok"] for c in fit.get("checks", [])}
            if fit.get("eligibility") == "incompatible" or any(v is False for v in checks.values()):
                importance = "quiet"
            elif (fit.get("score") or 0) >= 75 and checks.get("days", True) is True:
                importance = "urgent"
            else:
                importance = "brief"
            rec.add("new_job", importance, f"new:{j.id}", f"新岗位：{label_of(j)}", ref=ref, match_score=fit.get("score"),
                    eligibility=fit.get("eligibility"), days_per_week=j.days_per_week, city=j.city, label=label_of(j))
        else:
            rec.add("scope_entered", "quiet", f"entered:{j.id}:{w.version}", f"{label_of(j)} 现在符合你的范围了", ref=ref,
                    label=label_of(j))
    for job_id in prev - set(active) - targets:
        job = s.get(Job, job_id)
        if job is not None and job.status == "gone":
            rec.add("scope_closed", "brief", f"scope_closed:{job.id}:{_digest(job.last_seen_at)}",
                    f"范围内岗位下架：{label_of(job)}", ref=f"J-{job.id}", label=label_of(job))
    w.state = {**w.state, "scope_active": sorted(active)}
    return {"active": len(active), "entered": len(entered)}


def scope_companies(s: Session, criteria: dict[str, Any]) -> list[str]:
    rows = s.execute(scope_query(criteria).where(Job.status == "active").with_only_columns(Job.company).distinct())
    return sorted({c for (c,) in rows if c})


def _lower(w: Watch, since: datetime) -> datetime:
    """按入库时间找新内容的下界：往前多看一点防漏，但不早于开卡时刻（开卡前就有的不算“新”）。"""
    return max(since - OVERLAP, w.baseline_at)


def _check_materials(s: Session, w: Watch, rec: Recorder, since: datetime, companies: list[str]) -> None:
    if not companies:
        return
    stmt = (select(Post).where(Post.post_type.in_(("面经", "内推", "招聘")), Post.created_at > _lower(w, since),
                               Post.published_at >= rec.now - timedelta(days=MATERIAL_DAYS),
                               or_(Post.big_companies.overlap(companies), Post.companies.overlap(companies))))
    if w.criteria.get("direction"):
        d = w.criteria["direction"]
        stmt = stmt.where(or_(any_(Post.directions) == d, Post.summary.ilike(f"%{d}%")))
    for p in s.scalars(stmt.order_by(Post.published_at.desc()).limit(30)):
        first = next((line for line in (p.summary or "").splitlines() if line.strip()), "")
        rec.add("new_material", "brief", f"post:{p.id}", f"新{p.post_type}：{first[:60]}", ref=f"P-{p.id}", label=first[:40],
                post_type=p.post_type, companies=p.big_companies, published=p.published_at.isoformat() if p.published_at else None,
                questions=len(p.interview_questions or []))


def _check_peers(s: Session, w: Watch, rec: Recorder, since: datetime, companies: list[str]) -> None:
    d = w.criteria.get("direction")
    if not d and not companies:
        return
    stmt = select(PeerProfile).where(PeerProfile.created_at > _lower(w, since), PeerProfile.outcome.in_(("拿到offer", "挂了")),
                                     PeerProfile.published_at >= rec.now - timedelta(days=PEER_DAYS))
    # 有方向时按方向找（同方向的人在哪家都有参考价值）；没写方向才按范围里的公司找
    if d:
        stmt = stmt.where(or_(PeerProfile.direction.ilike(f"%{d}%"), PeerProfile.target_role.ilike(f"%{d}%")))
    else:
        stmt = stmt.where(or_(PeerProfile.big_company.in_(companies), PeerProfile.target_company.in_(companies)))
    for p in s.scalars(stmt.order_by(PeerProfile.published_at.desc()).limit(20)):
        bg = "、".join(x for x in (p.stage if p.stage != "未说明" else None, p.school) if x) or "背景没写"
        rec.add("new_peer", "brief", f"peer:{p.id}", f"同类样本：{p.big_company or p.target_company or '公司未知'} "
                f"{p.target_role or p.direction or ''} {p.outcome}（{bg}）", ref=f"R-{p.raw_item_id}",
                outcome=p.outcome, stage=p.stage, school=p.school, reached=p.reached)


def coverage(s: Session, now: datetime) -> list[dict[str, Any]]:
    """各来源最近一次成功的时间和问题。演示没有采集，不报覆盖。"""
    from polaris.daemon import OFFICIAL_RAW

    if get_settings().demo_mode:
        return []
    out = []
    runs = s.execute(select(Run.params["source"].astext, Run.status, Run.started_at, Run.stats)
                     .where(Run.kind == "collect", Run.started_at >= now - timedelta(days=7))
                     .order_by(Run.started_at.desc())).all()
    for source in OFFICIAL_RAW:
        ok = [r for r in runs if r[0] == source and r[1] == "ok"]
        last = ok[0][2] if ok else None
        if last is None or now - last > OFFICIAL_STALE:
            out.append({"source": source, "label": f"官网 {source}", "last_ok": last.isoformat() if last else None,
                        "problem": "超过 36 小时没有成功重采，这期间的下架和新增判断不可靠"})
    for source, name in LOGIN_SOURCES.items():
        mine = [r for r in runs if r[0] == source]
        latest = mine[0] if mine else None
        if latest is None or now - latest[2] > PLATFORM_STALE:
            out.append({"source": source, "label": name, "last_ok": None, "problem": "最近一天没有采集记录"})
        elif (latest[3] or {}).get("stopped"):
            out.append({"source": source, "label": name, "last_ok": latest[2].isoformat(),
                        "problem": f"最近一轮被平台拦下（{latest[3]['stopped']}），需要你在采集窗口里处理"})
    return out


def _check_coverage(s: Session, rec: Recorder) -> list[dict[str, Any]]:
    gaps = coverage(s, rec.now)
    day = rec.now.astimezone(BEIJING).date().isoformat()
    for g in gaps:
        rec.add("source_gap", "brief", f"gap:{g['source']}:{day}", f"{g['label']}没查成：{g['problem']}", **g)
    return gaps


def check(s: Session, w: Watch, now: datetime | None = None,
          notify_fn: Callable[[str, str], None] | None = None) -> dict[str, Any]:
    """检查一次；调用方负责事务。只读本地库、不调模型。"""
    now = now or datetime.now(UTC)
    if w.status != "active":
        return {"skipped": f"跟进卡是{w.status}状态"}
    s.execute(select(Watch.id).where(Watch.id == w.id).with_for_update())  # 常驻采集和网页“立即检查”不同时查同一张卡
    since = w.last_check_at or w.baseline_at
    rec = Recorder(s, w, now)
    targets = _check_targets(s, w, rec)
    scope = _check_scope(s, w, rec)
    companies = scope_companies(s, w.criteria)
    _check_materials(s, w, rec, since, companies)
    _check_peers(s, w, rec, since, companies)
    gaps = _check_coverage(s, rec)
    w.last_check_at, w.next_check_at, w.last_error = now, now + timedelta(minutes=w.cadence_minutes), None
    notified = notify_urgent(s, w, now, notify_fn)
    return {"watch": w.id, "new_events": rec.added, "targets": targets, "scope": scope, "coverage_gaps": len(gaps),
            "notified": notified}


def notify_urgent(s: Session, w: Watch, now: datetime, notify_fn: Callable[[str, str], None] | None = None) -> int:
    """当前版本里还没提醒过的紧急事件：系统通知一次。改过条件的旧版本事件不再提醒。"""
    pending = s.scalars(select(WatchEvent).where(WatchEvent.watch_id == w.id, WatchEvent.importance == "urgent",
                                                 WatchEvent.watch_version == w.version, WatchEvent.notified_at.is_(None))
                        .order_by(WatchEvent.id)).all()
    if not pending:
        return 0
    if w.notify == "desktop" and not get_settings().demo_mode:
        if notify_fn is None:
            from polaris.logins import notify as notify_fn
        text = "；".join(e.title for e in pending[:3]) + (f" 等 {len(pending)} 件" if len(pending) > 3 else "")
        notify_fn(f"北极星跟进：{w.title}", text)
    for e in pending:
        e.notified_at = now
    return len(pending)


def due(w: Watch, now: datetime) -> bool:
    return w.status == "active" and (w.next_check_at is None or w.next_check_at <= now)


def report_due(w: Watch, now: datetime) -> bool:
    """每天 report_hour 之后、至少查过一次，且今天还没写过。"""
    local = now.astimezone(BEIJING)
    if w.status != "active" or w.last_check_at is None or local.hour < w.report_hour:
        return False
    return w.last_report_at is None or w.last_report_at.astimezone(BEIJING).date() < local.date()


def run_due(now: datetime | None = None, echo: Callable[[str], None] = print,
            notify_fn: Callable[[str, str], None] | None = None, llm: Any = None) -> dict[str, int]:
    """常驻采集每轮调用：到期的卡查一次，到点的写报告，过期的写最后一份报告后结束。一张卡出错不影响别的。"""
    from polaris.db.session import session_scope
    from polaris.watch_report import write_report

    now = now or datetime.now(UTC)
    stats = {"checked": 0, "reports": 0, "ended": 0, "errors": 0}
    with session_scope() as s:
        ids = s.scalars(select(Watch.id).where(Watch.status == "active").order_by(Watch.id)).all()
    for watch_id in ids:
        try:
            with session_scope() as s:
                w = s.get(Watch, watch_id)
                expired = w.until is not None and now.astimezone(BEIJING).date() > w.until
                if due(w, now) or expired:
                    out = check(s, w, now, notify_fn)
                    stats["checked"] += 1
                    if out.get("new_events"):
                        echo(f"[跟进] {w.title}：{out['new_events']} 件新事，提醒 {out['notified']} 件")
                if report_due(w, now) or (expired and w.last_check_at):
                    write_report(s, w, now, llm=llm)
                    stats["reports"] += 1
                if expired:
                    w.status = "ended"
                    stats["ended"] += 1
                    echo(f"[跟进] {w.title} 到期结束")
        except Exception as e:  # noqa: BLE001  一张卡出错不影响别的卡和采集
            stats["errors"] += 1
            with session_scope() as s:
                w = s.get(Watch, watch_id)
                if w is not None:
                    w.last_error = f"{type(e).__name__}: {e}"[:500]
                    w.next_check_at = now + timedelta(minutes=w.cadence_minutes)
            echo(f"[跟进] 第 {watch_id} 张卡出错：{e}"[:200])
    return stats


def view(s: Session, w: Watch, *, events: int = 50) -> dict[str, Any]:
    """网页和工具看的完整样子。"""
    from polaris.db.models import WatchReport

    targets = s.scalars(select(WatchTarget).where(WatchTarget.watch_id == w.id).order_by(WatchTarget.id)).all()
    evs = s.scalars(select(WatchEvent).where(WatchEvent.watch_id == w.id).order_by(WatchEvent.id.desc()).limit(events)).all()
    report = s.scalar(select(WatchReport).where(WatchReport.watch_id == w.id).order_by(WatchReport.id.desc()).limit(1))
    unread = s.scalar(select(WatchEvent.id).where(WatchEvent.watch_id == w.id, WatchEvent.read_at.is_(None),
                                                  WatchEvent.importance != "quiet").limit(1))
    return {
        "id": w.id, "title": w.title, "criteria": w.criteria, "criteria_text": describe(w.criteria), "status": w.status,
        "until": w.until.isoformat() if w.until else None, "cadence_minutes": w.cadence_minutes, "report_hour": w.report_hour,
        "notify": w.notify, "version": w.version, "baseline_at": w.baseline_at.isoformat(),
        "last_check_at": w.last_check_at.isoformat() if w.last_check_at else None,
        "next_check_at": w.next_check_at.isoformat() if w.next_check_at else None,
        "last_report_at": w.last_report_at.isoformat() if w.last_report_at else None, "last_error": w.last_error,
        "scope_active": len(w.state.get("scope_active") or []) if w.state.get("scope_active") is not None else None,
        "has_unread": unread is not None,
        "targets": [{"job_id": t.job_id, "ref": f"J-{t.job_id}" if t.job_id else None, "label": t.label,
                     "snapshot": t.snapshot, "snapshot_at": t.snapshot_at.isoformat() if t.snapshot_at else None} for t in targets],
        "events": [{"id": e.id, "kind": e.kind, "kind_label": KIND_LABELS.get(e.kind, e.kind), "importance": e.importance,
                    "ref": e.ref, "title": e.title, "detail": e.detail, "version": e.watch_version,
                    "created_at": e.created_at.isoformat(), "notified_at": e.notified_at.isoformat() if e.notified_at else None,
                    "read": e.read_at is not None} for e in evs],
        "report": {"id": report.id, "markdown": report.markdown, "fallback": report.fallback,
                   "period_start": report.period_start.isoformat(), "period_end": report.period_end.isoformat(),
                   "created_at": report.created_at.isoformat()} if report else None,
    }
