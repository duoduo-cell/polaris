"""把所有 posting 对齐成岗位：同岗识别 → 字段裁决 → 写 jobs（按稳定编号增量更新），posting 指向它的岗位。

可重复跑：同一批数据跑两次结果一样；新采到的 posting 会并进已有岗位或新建岗位；没人引用的旧岗位删掉。
"""
from collections import Counter
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from polaris.align.match import PostingView, cluster, job_key, view
from polaris.align.resolve import RESOLVED_FIELDS, resolve
from polaris.companies import load as load_companies
from polaris.db.models import Company, Job, Posting, RawItem
from polaris.db.session import session_scope

FIELD_COLUMNS = {f: f for f in RESOLVED_FIELDS} | {"city": "city"}


def load_views() -> list[PostingView]:
    with session_scope() as s:
        rows = s.execute(select(Posting, RawItem).join(RawItem, RawItem.id == Posting.raw_item_id)).all()
        views = []
        for p, r in rows:
            v = view(p, r)
            v.fields = {f: getattr(p, f) for f in RESOLVED_FIELDS} | {
                "directions": p.directions, "requirements": p.requirements, "jd_text": p.jd_text}
            views.append(v)
    return views


def ensure_companies() -> dict[str, int]:
    rows = [{"name": c.name, "aliases": list(c.aliases), "is_big": True} for c in load_companies()]
    with session_scope() as s:
        s.execute(insert(Company).values(rows).on_conflict_do_nothing(index_elements=["name"]))
        return dict(s.execute(select(Company.name, Company.id)).all())


def run_align() -> dict[str, Any]:
    views = load_views()
    groups = cluster(views)
    company_ids = ensure_companies()
    stats: Counter = Counter()
    keys_seen: set[str] = set()
    with session_scope() as s:
        for g in groups:
            k = job_key(g)
            if k in keys_seen:  # 极少数情况下两组算出同一个编号：后一组加上最小 posting 编号区分
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
                "status": "active", "is_synthetic": all(v.is_synthetic for v in g),
                **{col: values.get(f) for f, col in FIELD_COLUMNS.items()},
            }
            stmt = insert(Job).values(row)
            stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={c: stmt.excluded[c] for c in row if c != "key"})
            job_id = s.execute(stmt.returning(Job.id)).scalar_one()
            s.execute(update(Posting).where(Posting.id.in_([v.id for v in g])).values(job_id=job_id))
            stats["jobs"] += 1
            stats["merged_jobs"] += len(g) > 1
            stats["conflicts"] += len(conflicts)
            for c in conflicts:
                stats[f"conflict:{c['field']}"] += 1
        stale = s.execute(delete(Job).where(Job.key.not_in(keys_seen)).returning(Job.id)).scalars().all()
    stats["postings"] = len(views)
    stats["stale_removed"] = len(stale)
    return dict(stats)
