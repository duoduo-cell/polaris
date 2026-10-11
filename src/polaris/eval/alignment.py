"""对齐的评测：同岗识别对标准答案（成对的精确率、召回率、F1），冲突发现（合成数据的转载版），出处完整率。"""
import json
from typing import Any

from sqlalchemy import select

from polaris.config import ROOT
from polaris.db.models import Job, Posting, RawItem
from polaris.db.session import session_scope
from polaris.synth.jobs import read_fixture

GOLD = ROOT / "eval" / "gold" / "same_job_v1.jsonl"  # 开发集：调规则时看过
HOLDOUT = ROOT / "eval" / "gold" / "same_job_holdout_v1.jsonl"  # 留出集：第一版规则冻结后标，暴露问题后用来改过规则
TEST = ROOT / "eval" / "gold" / "same_job_test_v1.jsonl"  # 测试集：第二版规则冻结后标，只评不调


def _posting_jobs() -> dict[str, int | None]:
    """来源:编号 → 它被归到的岗位。"""
    with session_scope() as s:
        rows = s.execute(select(RawItem.source, RawItem.external_id, Posting.job_id)
                         .join(Posting, Posting.raw_item_id == RawItem.id)).all()
    return {f"{src}:{ext}": job for src, ext, job in rows}


def pairwise(gold_path=GOLD) -> dict[str, Any]:
    gold = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    jobs = _posting_jobs()
    tp = fp = fn = tn = 0
    errors = []
    unsure = 0
    for g in gold:
        ja, jb = jobs.get(g["a"]), jobs.get(g["b"])
        if ja is None or jb is None:
            continue
        if g.get("unsure"):  # 标注时拿不准的对不计分，单独报数量
            unsure += 1
            continue
        pred = ja == jb
        if pred and g["same"]:
            tp += 1
        elif pred and not g["same"]:
            fp += 1
            errors.append({"type": "错并", **g})
        elif not pred and g["same"]:
            fn += 1
            errors.append({"type": "漏并", **g})
        else:
            tn += 1
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p and r else None
    return {"pairs": tp + fp + fn + tn, "positives": tp + fn, "unsure": unsure, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": p, "recall": r, "f1": f1, "errors": errors}


CONFLICT_FIELDS = {"salary": ("salary_min", "salary_max", "salary_text"), "deadline": ("deadline",),
                   "days_per_week": ("days_per_week",)}


def conflict_detection() -> dict[str, Any]:
    """合成数据里每个转载版都故意改了一个字段：看合并后的岗位有没有把这个矛盾记下来。"""
    jobs = _posting_jobs()
    with session_scope() as s:
        conflicts = dict(s.execute(select(Job.id, Job.conflicts)).all())
    found = total = 0
    missed = []
    for r in read_fixture():
        if not r["meta"]["dup_of"] or not r["meta"]["checked"]:
            continue
        total += 1
        job = jobs.get(f"{r['source']}:{r['id']}")
        fields = {c["field"] for c in conflicts.get(job, [])}
        if fields & set(CONFLICT_FIELDS[r["meta"]["conflict_field"]]):
            found += 1
        else:
            missed.append({"id": r["id"], "field": r["meta"]["conflict_field"], "job_conflicts": sorted(fields)})
    return {"total": total, "found": found, "recall": found / total if total else None, "missed": missed}


def provenance() -> dict[str, Any]:
    """每个岗位的每个有值的字段，都能追到至少一条还存在的 posting。"""
    with session_scope() as s:
        jobs = s.execute(select(Job.id, Job.field_sources)).all()
        posting_ids = set(s.execute(select(Posting.id)).scalars())
    fields = ok = 0
    for _, src in jobs:
        for info in (src or {}).values():
            fields += 1
            ok += bool(info.get("postings")) and set(info["postings"]) <= posting_ids
    return {"jobs": len(jobs), "fields": fields, "traceable": ok, "rate": ok / fields if fields else None}
