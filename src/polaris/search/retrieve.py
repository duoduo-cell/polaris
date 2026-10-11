"""混合检索流水线（`TECH.md` 第 4 节）。参数起点来自 WeKnora 的 `04-rag-pipeline.md`，再用我们的标准答案集调。

1. 结构化过滤：类型（岗位 / 帖子）、公司、时间、是否合成数据，在 Postgres 里先筛
2. 三路召回：关键词（pg_search BM25，jieba 分词）、向量（pgvector，余弦）、图谱（Neo4j 一跳邻居，可关）
3. 关键词召回太少时做查询扩展：把问题里的公司换成它的各种叫法（字节 → 字节跳动、ByteDance、抖音……），阈值放宽到 0.8 倍
4. 加权 RRF 融合，归一到 0 到 1
5. 重排（百炼 qwen3-rerank）：低于阈值的丢掉，但保底留第一名；重排失败退回融合分
6. 综合分 = 0.6 × 重排分 + 0.3 × 融合分 + 0.1 × 来源权重
7. MMR 去冗余（同一道面试题被很多帖子转载），相似度用分词后的 Jaccard
8. 截断到 top_k；同分按块编号排，结果可复现

每一步的条数和耗时记在 trace 里，回答时能说清"为什么是这几条"。
"""
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from polaris.companies import match as big_company
from polaris.db.models import SearchChunk
from polaris.db.session import session_scope
from polaris.llm.client import LLM, LLMError

RRF_K = 60
WEIGHTS = {"vector": 0.7, "keyword": 0.3, "graph": 0.3}  # 图谱一路先和关键词同权，待标准答案调
RECALL_K = 50  # 每一路召回多少
VECTOR_MIN = 0.15  # 余弦相似度下限
KEYWORD_MIN = 0.3  # BM25 分数相对本次第一名的比例下限
EXPAND_BELOW = 5  # 关键词召回少于这么多条就做查询扩展
RERANK_MAX = 50  # 最多送多少条去重排
# 重排分下限。WeKnora 的 0.2 是别的重排模型的；10-11 在检索标准答案的 973 个评过的候选上看 qwen3-rerank 的分数：
# 不相关的中位数 0.49、部分相关 0.65、完全相关 0.84，三档重叠很多，阈值只能修掉尾巴。0.35 去掉 20 条不相关，
# 误伤 5 条部分相关、0 条完全相关；再高就开始丢完全相关的。注意是在评测用的同一批数据上选的
RERANK_MIN = 0.35
RERANK_KEEP_TOP1 = 0.25  # 全部低于下限时，第一名不低于这个就保底留下（按 WeKnora 0.15/0.2 的比例，未单独标定）
MMR_LAMBDA = 0.7
SOURCE_WEIGHT = {"job": 1.0, "post": 0.9}
JOB_KINDS = ("job", "job_duty", "job_req")
POST_KINDS = ("post", "post_part", "question")


@dataclass
class SearchQuery:
    text: str
    scope: Literal["all", "jobs", "posts", "questions"] = "all"
    company: str | None = None
    since: datetime | None = None
    include_synthetic: bool = False
    top_k: int = 10
    rerank: bool = True
    mmr: bool = True
    channels: tuple[str, ...] = ("keyword", "vector", "graph")


@dataclass
class Hit:
    chunk_id: int
    key: str
    kind: str
    job_id: int | None
    post_id: int | None
    header: str
    body: str
    ranks: dict[str, int] = field(default_factory=dict)  # 每一路里的名次（从 1 开始）
    fused: float = 0.0
    rerank: float | None = None
    final: float = 0.0

    @property
    def doc(self) -> str:
        """块属于哪个文档：一个岗位、一篇帖子，或一道面试题（题目本身就是要找的东西）。评测按文档算。"""
        if self.kind == "question":
            return f"q:{self.key}"
        return f"job:{self.job_id}" if self.job_id else f"post:{self.post_id}"

    @property
    def source_weight(self) -> float:
        return SOURCE_WEIGHT["job" if self.job_id else "post"]


def _filters(q: SearchQuery) -> list[Any]:
    conds: list[Any] = []
    if not q.include_synthetic:
        conds.append(SearchChunk.is_synthetic.is_(False))
    kinds = {"jobs": JOB_KINDS, "posts": POST_KINDS, "questions": ("question",)}.get(q.scope)
    if kinds:
        conds.append(SearchChunk.kind.in_(kinds))
    if q.company:
        big = big_company(q.company)
        conds.append(SearchChunk.companies.any(big.name if big else q.company))
    if q.since:
        conds.append(SearchChunk.published_at >= q.since)
    return conds


def expand(query: str) -> str:
    """查询扩展：问题里提到的大厂，补上它的其他叫法（只加在关键词一路）。"""
    big = big_company(query)
    if not big:
        return query
    extra = [n for n in big.names if n.lower() not in query.lower()]
    return " ".join([query, *extra])


def keyword(s: Session, q: SearchQuery, query: str, min_ratio: float = KEYWORD_MIN) -> list[tuple[int, float]]:
    score = func.pdb.score(SearchChunk.id).label("score")
    rows = s.execute(select(SearchChunk.id, score).where(SearchChunk.content.op("|||")(query), *_filters(q))
                     .order_by(score.desc(), SearchChunk.id).limit(RECALL_K)).all()
    if not rows:
        return []
    top = rows[0].score or 1.0
    return [(r.id, float(r.score)) for r in rows if r.score >= top * min_ratio]


def vector(s: Session, q: SearchQuery, vec: list[float]) -> list[tuple[int, float]]:
    s.execute(text("SET LOCAL hnsw.ef_search = 200"))
    s.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))  # 带过滤条件时继续往下找，不会少返回（pgvector 0.8）
    dist = SearchChunk.embedding.cosine_distance(vec).label("dist")
    rows = s.execute(select(SearchChunk.id, dist).where(SearchChunk.embedding.is_not(None), *_filters(q))
                     .order_by(dist, SearchChunk.id).limit(RECALL_K)).all()
    return [(r.id, 1 - float(r.dist)) for r in rows if 1 - r.dist >= VECTOR_MIN]


def graph(q: SearchQuery) -> list[tuple[int, float]]:
    """图谱一路：问题里的实体 → Neo4j 一跳邻居 → 关联的块。图谱没开或连不上就返回空，不影响另外两路。"""
    try:
        from polaris.graph.retrieve import graph_candidates
    except ImportError:
        return []
    try:
        return graph_candidates(q.text, scope=q.scope, company=q.company)
    except Exception:  # noqa: BLE001  图谱是可关的增强，出错就当没有
        return []


def rrf(channels: dict[str, list[tuple[int, float]]]) -> dict[int, tuple[float, dict[str, int]]]:
    """加权 RRF：score = Σ w / (k + rank)，再除以理论最大值归一到 0 到 1。"""
    fused: dict[int, float] = {}
    ranks: dict[int, dict[str, int]] = {}
    for name, items in channels.items():
        for rank, (cid, _) in enumerate(items, 1):
            fused[cid] = fused.get(cid, 0.0) + WEIGHTS[name] / (RRF_K + rank)
            ranks.setdefault(cid, {})[name] = rank
    best = sum(WEIGHTS[n] for n in channels) / (RRF_K + 1)
    return {cid: (score / best, ranks[cid]) for cid, score in fused.items()}


def _load(s: Session, ids: list[int]) -> dict[int, Hit]:
    rows = s.execute(select(SearchChunk).where(SearchChunk.id.in_(ids))).scalars().all()
    return {r.id: Hit(chunk_id=r.id, key=r.key, kind=r.kind, job_id=r.job_id, post_id=r.post_id, header=r.header,
                      body=r.body) for r in rows}


def _tokens(s: Session, ids: list[int]) -> dict[int, frozenset[str]]:
    """用和 BM25 索引同一个分词器（pg_search 的 jieba）切词，去掉标点和空白。"""
    rows = s.execute(text("SELECT id, content::pdb.jieba::text[] AS toks FROM chunks WHERE id = ANY(:ids)"), {"ids": ids}).all()
    return {r.id: frozenset(t for t in r.toks if t.strip() and any(ch.isalnum() for ch in t)) for r in rows}


def mmr(hits: list[Hit], tokens: dict[int, frozenset[str]], k: int, lam: float = MMR_LAMBDA) -> list[Hit]:
    def sim(a: Hit, b: Hit) -> float:
        x, y = tokens.get(a.chunk_id, frozenset()), tokens.get(b.chunk_id, frozenset())
        return len(x & y) / len(x | y) if x | y else 0.0

    chosen: list[Hit] = []
    pool = sorted(hits, key=lambda h: (-h.final, h.chunk_id))
    while pool and len(chosen) < k:
        best = max(pool, key=lambda h: (lam * h.final - (1 - lam) * max((sim(h, c) for c in chosen), default=0.0),
                                        -h.chunk_id))
        chosen.append(best)
        pool.remove(best)
    return chosen


def _rerank(llm: LLM, query: str, hits: list[Hit], trace: dict[str, Any]) -> list[Hit]:
    try:
        scores = llm.rerank(query, [f"{h.header}\n{h.body}"[:1500] for h in hits], purpose="search_rerank")
    except (LLMError, Exception) as e:  # noqa: BLE001  重排失败退回融合分
        trace["rerank_error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return hits
    for idx, score in scores:
        hits[idx].rerank = score
    kept = [h for h in hits if h.rerank is not None and h.rerank >= RERANK_MIN]
    if not kept:
        top = max(hits, key=lambda h: (h.rerank or 0.0, -h.chunk_id))
        kept = [top] if (top.rerank or 0.0) >= RERANK_KEEP_TOP1 else []
        trace["rerank_fallback_top1"] = bool(kept)
    trace["after_rerank"] = len(kept)
    return kept


_QUERY_VECTORS: dict[str, list[float]] = {}


def _query_vector(llm: LLM, text_: str) -> list[float]:
    """问题的向量在进程内缓存：评测时同一个问题要跑好几种配置。"""
    if text_ not in _QUERY_VECTORS:
        _QUERY_VECTORS[text_] = llm.embed([text_], purpose="search_query")[0]
    return _QUERY_VECTORS[text_]


def search(q: SearchQuery, llm: LLM | None = None) -> tuple[list[Hit], dict[str, Any]]:
    llm = llm or LLM()
    trace: dict[str, Any] = {"query": q.text, "scope": q.scope}
    t0 = time.perf_counter()
    channels: dict[str, list[tuple[int, float]]] = {}
    with session_scope() as s:
        if "keyword" in q.channels:
            channels["keyword"] = keyword(s, q, q.text)
            if len(channels["keyword"]) < EXPAND_BELOW and (expanded := expand(q.text)) != q.text:
                channels["keyword"] = keyword(s, q, expanded, KEYWORD_MIN * 0.8)
                trace["expanded"] = expanded
        if "vector" in q.channels:
            try:
                channels["vector"] = vector(s, q, _query_vector(llm, q.text))
            except LLMError as e:
                trace["vector_error"] = str(e)[:120]
        if "graph" in q.channels:
            recalled = graph(q)
            # 图谱只负责找关联；日期、类型、公司和合成标记仍以 Postgres 为准。
            # 必须在融合前过滤，否则无效候选还会占名次、影响 RRF 分数。
            allowed = set(s.scalars(select(SearchChunk.id).where(
                SearchChunk.id.in_([cid for cid, _ in recalled]), *_filters(q)))) if recalled else set()
            channels["graph"] = [(cid, score) for cid, score in recalled if cid in allowed]
        channels = {k: v for k, v in channels.items() if v}
        trace["recall"] = {k: len(v) for k, v in channels.items()}
        fused = rrf(channels)
        ids = sorted(fused, key=lambda c: (-fused[c][0], c))
        hits_by_id = _load(s, ids)
        hits = []
        for cid in ids:
            if cid in hits_by_id:
                h = hits_by_id[cid]
                h.fused, h.ranks = fused[cid]
                hits.append(h)
        trace["fused"] = len(hits)
        candidates = hits[:RERANK_MAX]
        if q.rerank and llm.settings.rerank_enabled and candidates:
            candidates = _rerank(llm, q.text, candidates, trace)
        for h in candidates:
            h.final = (0.6 * h.rerank + 0.3 * h.fused if h.rerank is not None else 0.9 * h.fused) + 0.1 * h.source_weight
        if q.mmr:
            out = mmr(candidates, _tokens(s, [h.chunk_id for h in candidates]), q.top_k)
        else:
            out = sorted(candidates, key=lambda h: (-h.final, h.chunk_id))[: q.top_k]
    trace["returned"] = len(out)
    trace["ms"] = int((time.perf_counter() - t0) * 1000)
    return out, trace


def docs(hits: list[Hit]) -> list[str]:
    """按名次列出命中的文档（同一文档只算第一次出现）。"""
    seen: dict[str, None] = {}
    for h in hits:
        seen.setdefault(h.doc, None)
    return list(seen)


__all__ = ["Hit", "SearchQuery", "docs", "expand", "rrf", "search"]
