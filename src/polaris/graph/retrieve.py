"""图谱一路召回（`TECH.md` 第 4 节第 3 步）：问题里的实体 → Neo4j 一跳邻居 → 关联的岗位、帖子、面试题 → 对应的块。

做法参考 WeKnora 的图谱检索（实体名包含匹配 → 一跳邻居，限制种子数和行数），打分是自己定的：
一个文档连到的种子越多、种子越具体，排得越前。种子权重：部门 2（最具体）、公司 1、技能 1、城市 0.5、方向 0.5。
只命中方向、城市这类宽泛种子（总权重不到 1）时不召回：那样会拉出几百个只沾边的岗位。
这一路擅长的是"多个条件同时满足"的问题，比如"腾讯要 RAG 的岗位""字节问过 KV Cache 的面试"。
"""
from typing import Any

from sqlalchemy import select

from polaris.companies import _patterns
from polaris.db.models import SearchChunk
from polaris.db.session import session_scope
from polaris.graph.client import driver, enabled
from polaris.vocab import skills_in

SEED_WEIGHT = {"Unit": 2.0, "Company": 1.0, "Skill": 1.0, "City": 0.5, "Direction": 0.5}
MIN_SEED_WEIGHT = 1.0
MAX_SEEDS = 200
MAX_ROWS = 2000
MAX_DOCS = 30
DOC_LABELS = {"jobs": ["Job"], "posts": ["Post", "Question"], "questions": ["Question"], "all": ["Job", "Post", "Question"]}


def companies_in(text: str) -> list[str]:
    return [c.name for c, pats in _patterns() if any(p.search(text) for p in pats)]


SEEDS = """
UNWIND $companies AS name MATCH (n:Company {name: name}) RETURN elementId(n) AS id, 'Company' AS label, n.name AS name
UNION
UNWIND $skills AS name MATCH (n:Skill {name: name}) RETURN elementId(n) AS id, 'Skill' AS label, n.name AS name
UNION
MATCH (n:Unit) WHERE size(n.name) >= 2 AND toLower($q) CONTAINS toLower(n.name)
  AND (size($companies) = 0 OR n.company IN $companies)
RETURN elementId(n) AS id, 'Unit' AS label, n.key AS name
UNION
MATCH (n) WHERE (n:City OR n:Direction) AND size(n.name) >= 2 AND toLower($q) CONTAINS toLower(n.name)
RETURN elementId(n) AS id, labels(n)[0] AS label, n.name AS name
"""

EXPAND = """
UNWIND $seeds AS seed
MATCH (d)-[]->(n) WHERE elementId(n) = seed.id AND any(l IN labels(d) WHERE l IN $labels)
WITH d, collect(DISTINCT seed) AS hit LIMIT $max_rows
WITH d, hit, reduce(s = 0.0, x IN hit | s + x.weight) AS score
RETURN labels(d)[0] AS label, coalesce(d.id, d.key) AS ref, score, [x IN hit | x.name] AS via,
       coalesce(d.posted_at, d.published_at, '') AS at
ORDER BY score DESC, at DESC, ref
LIMIT $max_docs
"""


def find_seeds(text: str, company: str | None = None) -> list[dict[str, Any]]:
    companies = companies_in(text) + ([company] if company else [])
    companies = [c for i, c in enumerate(companies) if c not in companies[:i]]
    rows = driver().execute_query(SEEDS, q=text, companies=companies, skills=skills_in(text)).records
    seeds = [{"id": r["id"], "label": r["label"], "name": r["name"], "weight": SEED_WEIGHT[r["label"]]} for r in rows]
    return seeds[:MAX_SEEDS]


def graph_docs(text: str, scope: str = "all", company: str | None = None) -> tuple[list[dict[str, Any]], list[dict]]:
    """返回（按分数排好的文档，种子）。文档：{label, ref, score, via}。"""
    if not enabled():
        return [], []
    seeds = find_seeds(text, company)
    if sum(s["weight"] for s in seeds) < MIN_SEED_WEIGHT:
        return [], seeds
    rows = driver().execute_query(EXPAND, seeds=seeds, labels=DOC_LABELS.get(scope, DOC_LABELS["all"]),
                                  max_rows=MAX_ROWS, max_docs=MAX_DOCS).records
    return [{"label": r["label"], "ref": r["ref"], "score": r["score"], "via": r["via"]} for r in rows], seeds


def graph_candidates(text: str, scope: str = "all", company: str | None = None) -> list[tuple[int, float]]:
    """图谱命中的文档换成块：岗位 → 它的职责和要求块，帖子 → 帖子块，面试题 → 题目块。顺序跟文档走。"""
    docs, _ = graph_docs(text, scope, company)
    if not docs:
        return []
    job_ids = [d["ref"] for d in docs if d["label"] == "Job"]
    post_ids = [d["ref"] for d in docs if d["label"] == "Post"]
    q_keys = [d["ref"] for d in docs if d["label"] == "Question"]
    with session_scope() as s:
        rows = s.execute(select(SearchChunk.id, SearchChunk.job_id, SearchChunk.post_id, SearchChunk.key, SearchChunk.kind)
                         .where(SearchChunk.job_id.in_(job_ids) | (SearchChunk.post_id.in_(post_ids)
                                                                  & SearchChunk.kind.in_(("post", "post_part")))
                                | SearchChunk.key.in_(q_keys))
                         .order_by(SearchChunk.id)).all()
    by_doc: dict[tuple[str, Any], list[int]] = {}
    for r in rows:
        ref = ("Question", r.key) if r.kind == "question" else ("Job", r.job_id) if r.job_id else ("Post", r.post_id)
        by_doc.setdefault(ref, []).append(r.id)
    out: list[tuple[int, float]] = []
    for d in docs:
        out += [(cid, float(d["score"])) for cid in by_doc.get((d["label"], d["ref"]), [])]
    return out
