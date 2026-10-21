"""把所有 posting 对齐成岗位：同岗识别 → 字段裁决 → 写 jobs（按稳定编号增量更新），posting 指向它的岗位。

可重复跑；旧岗位仅在具有对象版本许可且没有人工跟进／事件时清理。
"""
from collections import Counter
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, defer

from polaris.align.match import PostingView, cluster, job_key, view
from polaris.align.pruning import PruneBlocked, authorize, candidates
from polaris.align.resolve import RESOLVED_FIELDS, resolve
from polaris.companies import load as load_companies
from polaris.db.models import Company, Job, Posting, RawItem
from polaris.db.session import session_scope
from polaris.search.locking import acquire_knowledge_write

FIELD_COLUMNS = {f: f for f in RESOLVED_FIELDS} | {"city": "city"}


def load_views(session: Session | None = None) -> list[PostingView]:
    if session is None:
        with session_scope() as s:
            return load_views(s)
    rows = session.execute(select(Posting, RawItem).options(defer(Posting.embedding))
                           .join(RawItem, RawItem.id == Posting.raw_item_id)).all()
    views = []
    for p, r in rows:
        v = view(p, r)
        v.fields = {f: getattr(p, f) for f in RESOLVED_FIELDS} | {
            "directions": p.directions, "requirements": p.requirements, "jd_text": p.jd_text}
        views.append(v)
    return views


def ensure_companies(session: Session | None = None) -> dict[str, int]:
    if session is None:
        with session_scope() as s:
            return ensure_companies(s)
    rows = [{"name": c.name, "aliases": list(c.aliases), "is_big": True} for c in load_companies()]
    session.execute(insert(Company).values(rows).on_conflict_do_nothing(index_elements=["name"]))
    return dict(session.execute(select(Company.name, Company.id)).all())


OFFICIAL_DAILY = {"tencent": "tencent:careers", "bytedance": "bytedance:campus", "xhs_careers": "xhs:careers"}


def official_snapshots(session: Session | None = None) -> dict[str, Any]:
    """各官网最近一次成功的每日整批重采是什么时候开始的（daemon.official_daily）。"""
    from polaris.db.models import Run

    if session is None:
        with session_scope() as s:
            return official_snapshots(s)
    latest: dict[str, Any] = {}
    for params, started in session.execute(select(Run.params, Run.started_at).where(Run.kind == "collect", Run.status == "ok")):
        p = params or {}
        if p.get("mode") == "official-daily" and p.get("source") in OFFICIAL_DAILY:
            raw = OFFICIAL_DAILY[p["source"]]
            latest[raw] = max(latest.get(raw, started), started)
    return latest


def status_of(group: list[Any], snapshots: dict[str, Any]) -> str:
    """全是官网挂牌、而且每一条在它那个官网的每日重采里都超过一天没再出现，才算下架；别的来源没法判断，当作在招。"""
    from datetime import timedelta

    if not group or any(v.source not in snapshots or v.last_seen is None for v in group):
        return "active"
    return "gone" if all(v.last_seen < snapshots[v.source] - timedelta(days=1) for v in group) else "active"


def alignment_rows(views: list[PostingView], snapshots: dict[str, Any],
                   company_ids: dict[str, int]) -> list[tuple[dict[str, Any], list[int]]]:
    """纯计算：执行与只读诊断共享同岗分组、字段裁决和快照状态规则。"""
    groups = cluster(views)
    keys_seen: set[str] = set()
    rows = []
    for g in groups:
        k = job_key(g)
        if k in keys_seen:
            k = f"{k}#{min(v.id for v in g)}"[:300]
        keys_seen.add(k)
        values, sources, conflicts = resolve(g)
        longest = max(g, key=lambda v: len(v.fields.get("jd_text") or ""))
        directions = sorted({d for v in g for d in (v.fields.get("directions") or [])})
        row = {
            "key": k, "company": g[0].company, "unit": max((v.unit for v in g if v.unit), key=len, default=None),
            "cities": sorted(set().union(*(v.cities for v in g))), "posting_count": len(g), "conflicts": conflicts,
            "company_id": company_ids.get(g[0].company), "field_sources": sources,
            "company_name": g[0].company, "directions": directions, "jd_text": longest.fields.get("jd_text"),
            "requirements": longest.fields.get("requirements") or [],
            "first_seen_at": min(v.first_seen for v in g), "last_seen_at": max(v.last_seen for v in g),
            "status": status_of(g, snapshots), "is_synthetic": all(v.is_synthetic for v in g),
            **{col: values.get(f) for f, col in FIELD_COLUMNS.items()},
        }
        rows.append((row, [v.id for v in g]))
    return rows


def run_align(*, prune_approval: dict[str, str] | None = None,
              prune_generation: tuple[int, int] | None = None) -> dict[str, Any]:
    stats: Counter = Counter()
    with session_scope() as s:
        acquire_knowledge_write(s)
        views = load_views(s)
        rows = alignment_rows(views, official_snapshots(s), ensure_companies(s))
        keys_seen = {row["key"] for row, _ in rows}
        stale_jobs = candidates(s, keys_seen, lock=True)
        if not rows and stale_jobs:
            raise PruneBlocked("empty_alignment", len(stale_jobs))
        if stale_jobs and prune_generation is not None:
            from polaris.db.models import Run

            # 与新通知对 Run 的 UPDATE 串行；检查后的许可不会在提交前失效。
            run = s.scalar(select(Run).where(Run.id == prune_generation[0]).with_for_update(read=True))
            if run is None or run.params.get("generation") != prune_generation[1]:
                raise PruneBlocked("generation_changed", len(stale_jobs))
        authorize(s, stale_jobs, prune_approval)
        for row, posting_ids in rows:
            stmt = insert(Job).values(row)
            stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={c: stmt.excluded[c] for c in row if c != "key"})
            job_id = s.execute(stmt.returning(Job.id)).scalar_one()
            s.execute(update(Posting).where(Posting.id.in_(posting_ids)).values(job_id=job_id))
            stats["jobs"] += 1
            stats["gone"] += row["status"] == "gone"
            stats["merged_jobs"] += len(posting_ids) > 1
            stats["conflicts"] += len(row["conflicts"])
            for c in row["conflicts"]:
                stats[f"conflict:{c['field']}"] += 1
        # 固定本事务已锁定且核验的对象；不再按动态 NOT IN 扩大实际删除集合。
        stale = s.execute(delete(Job).where(Job.id.in_([j.id for j in stale_jobs])).returning(Job.id)).scalars().all()
    stats["postings"] = len(views)
    stats["stale_removed"] = len(stale)
    return dict(stats)
