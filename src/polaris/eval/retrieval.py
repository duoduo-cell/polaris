"""检索评测（M4）：同一组问题在不同检索配置下的 Recall@10、NDCG@10、MRR、MAP（WeKnora 用的同一套指标），做消融对比。

标准答案的做法（TREC 的 pooling + LLM 评审，参考 UMBRELA，Upadhyay 等 2024）：
1. 问题和"想找什么"（need）写在 eval/gold/retrieval_v1_queries.jsonl，人写的，公开；
2. 每个问题把各配置前 20 个文档汇成候选池；
3. 评审模型按 need 给每个候选打 0/1/2（不相关 / 部分相关 / 完全满足），结果缓存在 data/eval/（帖子是 B 档内容，
   标注里有帖子编号，不进仓库），评过的不再评；开发 agent 抽查一部分，报一致率；
4. 没进池子的文档按不相关算（pooling 的通常做法），报告里同时给出前 10 里有评审结果的比例。

NDCG 不惩罚重复：同一道面试题被五篇帖子转载，五条都算相关。所以另报"近重复率"：前 10 里和排在它前面的某条
几乎一样（字符三元组 Jaccard ≥ 0.7）的比例。MMR 牺牲一点 NDCG 换来的就是这个数下降。
"""
import json
import math
import time
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from polaris.config import ROOT
from polaris.db.models import Job, Post, RawItem, SearchChunk
from polaris.db.session import session_scope
from polaris.dedup import jaccard, shingle_set
from polaris.llm.client import LLM, parallel_map
from polaris.search.retrieve import SearchQuery, docs, search

QUERIES = ROOT / "eval" / "gold" / "retrieval_v1_queries.jsonl"
QRELS = ROOT / "data" / "eval" / "retrieval_v1_qrels.jsonl"
POOL_DEPTH = 20
CUTOFF = 10
JUDGE_BATCH = 8
NEAR_DUP = 0.7
SPOTCHECK = ROOT / "data" / "eval" / "retrieval_v1_spotcheck.jsonl"
PROMPT_VERSION = "retrieval-judge-v1"

CONFIGS: dict[str, dict[str, Any]] = {
    "关键词（BM25）": {"channels": ("keyword",), "rerank": False, "mmr": False},
    "向量": {"channels": ("vector",), "rerank": False, "mmr": False},
    "图谱": {"channels": ("graph",), "rerank": False, "mmr": False},
    "关键词+向量（RRF）": {"channels": ("keyword", "vector"), "rerank": False, "mmr": False},
    "三路（RRF）": {"channels": ("keyword", "vector", "graph"), "rerank": False, "mmr": False},
    "三路+重排": {"channels": ("keyword", "vector", "graph"), "rerank": True, "mmr": False},
    "三路+重排+MMR（完整）": {"channels": ("keyword", "vector", "graph"), "rerank": True, "mmr": True},
}


def load_queries() -> list[dict[str, Any]]:
    return [json.loads(line) for line in QUERIES.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---- 文档的稳定编号：库重建后自增编号会变，标注用岗位的稳定编号、帖子的来源编号 ----

def stable_refs() -> dict[str, str]:
    with session_scope() as s:
        jobs = dict(s.execute(select(Job.id, Job.key)).all())
        posts = {pid: f"{src}:{ext}" for pid, src, ext in s.execute(
            select(Post.id, RawItem.source, RawItem.external_id).join(RawItem, RawItem.id == Post.raw_item_id))}
        qkeys = s.execute(select(SearchChunk.key, SearchChunk.post_id).where(SearchChunk.kind == "question")).all()
    refs = {f"job:{i}": f"job:{k}" for i, k in jobs.items()} | {f"post:{i}": f"post:{r}" for i, r in posts.items()}
    refs |= {f"q:{k}": f"q:{posts.get(pid)}:{k.rsplit(':', 1)[1]}" for k, pid in qkeys}
    return refs


def doc_texts(doc_ids: list[str]) -> dict[str, str]:
    """评审时给模型看的内容：上下文头 + 正文开头。"""
    out: dict[str, str] = {}
    with session_scope() as s:
        for d in doc_ids:
            kind, _, ref = d.partition(":")
            if kind == "q":
                rows = s.execute(select(SearchChunk.header, SearchChunk.body).where(SearchChunk.key == ref)).all()
            elif kind == "job":
                rows = s.execute(select(SearchChunk.header, SearchChunk.body).where(SearchChunk.job_id == int(ref))
                                 .order_by(SearchChunk.id)).all()
            else:
                rows = s.execute(select(SearchChunk.header, SearchChunk.body).where(
                    SearchChunk.post_id == int(ref), SearchChunk.kind.in_(("post", "post_part"))).order_by(SearchChunk.id)).all()
            if rows:
                out[d] = (rows[0].header + "\n" + "\n".join(r.body for r in rows))[:900]
    return out


# ---- 评审 ----

class Grade(BaseModel):
    i: int = Field(..., description="候选的序号")
    grade: int = Field(..., ge=0, le=2, description="0 不相关，1 部分相关，2 完全满足")
    reason: str = Field(..., max_length=60, description="一句话理由")


class Grades(BaseModel):
    grades: list[Grade]


JUDGE_SYSTEM = """你是检索评测的评审。给你一个求职者的问题、他真正想找的东西（need），和几条候选（岗位、帖子或面试题）。
按 need 给每条候选打分：
2 = 完全满足 need 的每个条件（公司、方向、技能、城市、类型、内容都对得上）；
1 = 部分满足：主要条件对，但缺一个条件、或只是沾边（比如同公司但方向不同、同方向但不是 need 要的类型）；
0 = 不相关。
只看候选里写了什么，不要猜。每条都要打分。"""


def judge(llm: LLM, query: dict[str, Any], docs_: list[tuple[str, str]]) -> list[dict[str, Any]]:
    lines = [f"[{i}] {text}" for i, (_, text) in enumerate(docs_)]
    prompt = f"问题：{query['query']}\nneed：{query['need']}\n\n候选：\n" + "\n\n".join(lines)
    r = llm.extract(prompt, Grades, purpose="retrieval_judge", role="judge", system=JUDGE_SYSTEM,
                    prompt_version=PROMPT_VERSION, temperature=0)
    if not r.ok or r.value is None:
        raise RuntimeError(f"评审没给出合法结果：{r.error}")
    by_i = {g.i: g for g in r.value.grades}
    return [{"qid": query["qid"], "doc": ref, "grade": by_i[i].grade, "reason": by_i[i].reason}
            for i, (ref, _) in enumerate(docs_) if i in by_i]


def load_qrels() -> dict[tuple[str, str], dict[str, Any]]:
    if not QRELS.exists():
        return {}
    rows = [json.loads(line) for line in QRELS.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {(r["qid"], r["doc"]): r for r in rows}


# ---- 指标 ----

def metrics(ranked: list[str], rel: dict[str, int], k: int = CUTOFF) -> dict[str, float]:
    top = ranked[:k]
    relevant = {d for d, g in rel.items() if g >= 1}
    hits = [d for d in top if rel.get(d, 0) >= 1]
    dcg = sum((2 ** rel.get(d, 0) - 1) / math.log2(i + 2) for i, d in enumerate(top))
    ideal = sorted(rel.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    mrr = next((1 / (i + 1) for i, d in enumerate(top) if rel.get(d, 0) >= 1), 0.0)
    precisions = [len([x for x in top[: i + 1] if rel.get(x, 0) >= 1]) / (i + 1)
                  for i, d in enumerate(top) if rel.get(d, 0) >= 1]
    return {"recall": len(hits) / len(relevant) if relevant else 0.0, "ndcg": dcg / idcg if idcg else 0.0, "mrr": mrr,
            "map": sum(precisions) / min(len(relevant), k) if relevant else 0.0, "p": len(hits) / k}


def redundancy(ranked: list[str], shingles: dict[str, frozenset[str]], k: int = CUTOFF) -> float:
    top = [d for d in ranked[:k] if d in shingles]
    dups = sum(any(jaccard(shingles[d], shingles[e]) >= NEAR_DUP for e in top[:i]) for i, d in enumerate(top) if i)
    return dups / len(top) if top else 0.0


def spotcheck() -> dict[str, Any]:
    """开发 agent 抽查评审结果的一致率。"""
    if not SPOTCHECK.exists():
        return {}
    rows = [json.loads(line) for line in SPOTCHECK.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"n": len(rows), "exact": sum(r["judge"] == r["checker"] for r in rows),
            "binary": sum((r["judge"] >= 1) == (r["checker"] >= 1) for r in rows),
            "judge_higher": sum(r["judge"] > r["checker"] for r in rows), "judge_lower": sum(r["judge"] < r["checker"] for r in rows)}


def run_configs(queries: list[dict[str, Any]], llm: LLM, depth: int) -> dict[str, dict[str, list[str]]]:
    """每个配置、每个问题的文档排名（文档用稳定编号）。"""
    refs = stable_refs()
    out: dict[str, dict[str, list[str]]] = defaultdict(dict)
    timing: dict[str, list[int]] = defaultdict(list)
    for q in queries:
        for name, cfg in CONFIGS.items():
            t = time.perf_counter()
            hits, _ = search(SearchQuery(q["query"], scope=q["scope"], top_k=depth * 3, **cfg), llm=llm)
            timing[name].append(int((time.perf_counter() - t) * 1000))
            out[name][q["qid"]] = [refs.get(d, d) for d in docs(hits)][:depth]
    out["_timing"] = {k: sorted(v) for k, v in timing.items()}  # type: ignore[assignment]
    return out


def build_pool_and_judge(llm: LLM | None = None) -> dict[str, Any]:
    llm = llm or LLM()
    queries = load_queries()
    runs = run_configs(queries, llm, POOL_DEPTH)
    qrels = load_qrels()
    back = {v: k for k, v in stable_refs().items()}
    todo = []
    for q in queries:
        pool = sorted({d for name in CONFIGS for d in runs[name][q["qid"]]})
        new = [d for d in pool if (q["qid"], d) not in qrels]
        texts = doc_texts([back[d] for d in new if d in back])
        items = [(d, texts[back[d]]) for d in new if d in back and back[d] in texts]
        todo += [(q, items[i:i + JUDGE_BATCH]) for i in range(0, len(items), JUDGE_BATCH)]
    results = parallel_map(lambda t: judge(llm, *t), todo, llm.settings.llm_concurrency)
    QRELS.parent.mkdir(parents=True, exist_ok=True)
    added = 0
    with QRELS.open("a", encoding="utf-8") as f:
        for r in results:
            if isinstance(r, Exception):
                continue
            for row in r:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                added += 1
    return {"batches": len(todo), "failed": sum(isinstance(r, Exception) for r in results), "judged_new": added,
            "runs": runs}


def evaluate(runs: dict[str, dict[str, list[str]]] | None = None, llm: LLM | None = None) -> dict[str, Any]:
    queries = load_queries()
    runs = runs or run_configs(queries, llm or LLM(), POOL_DEPTH)
    qrels = load_qrels()
    rel: dict[str, dict[str, int]] = defaultdict(dict)
    for (qid, d), r in qrels.items():
        rel[qid][d] = r["grade"]
    back = {v: k for k, v in stable_refs().items()}
    all_docs = {d for name in CONFIGS for ranked in runs[name].values() for d in ranked[:CUTOFF]}
    texts = doc_texts([back[d] for d in all_docs if d in back])
    # 去掉第一行的上下文头再比：同一道题挂在不同帖子下，头里的日期不同，题目本身一样
    shingles = {d: shingle_set(texts[back[d]].split("\n", 1)[-1]) for d in all_docs if back.get(d) in texts}
    table: dict[str, dict[str, float]] = {}
    by_scope: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for name in CONFIGS:
        per_q = {q["qid"]: metrics(runs[name][q["qid"]], rel[q["qid"]]) for q in queries}
        judged = [sum((q["qid"], d) in qrels for d in runs[name][q["qid"]][:CUTOFF]) / max(1, len(runs[name][q["qid"]][:CUTOFF]))
                  for q in queries]
        table[name] = {m: sum(v[m] for v in per_q.values()) / len(per_q) for m in ("recall", "ndcg", "mrr", "map", "p")}
        table[name]["judged"] = sum(judged) / len(judged)
        table[name]["dup"] = sum(redundancy(runs[name][q["qid"]], shingles) for q in queries) / len(queries)
        t = runs["_timing"][name]  # type: ignore[index]
        table[name]["p50_ms"] = t[len(t) // 2]
        for scope in sorted({q["scope"] for q in queries}):
            qs = [q["qid"] for q in queries if q["scope"] == scope]
            by_scope[scope][name] = sum(per_q[x]["ndcg"] for x in qs) / len(qs)
    relevant = {qid: sum(g >= 1 for g in r.values()) for qid, r in rel.items()}
    grades = [r["grade"] for r in qrels.values()]
    return {"queries": len(queries), "qrels": len(qrels), "grades": {str(g): grades.count(g) for g in (0, 1, 2)},
            "relevant_per_query": relevant, "table": table, "ndcg_by_scope": dict(by_scope), "spotcheck": spotcheck()}
