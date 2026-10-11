"""把 jobs 和 posts 切成块写进 chunks 表，再给正文变过的块算向量。两步都可以重复跑：没变的不动。

关键词索引（pg_search BM25）随写入自动更新，不用单独重建。
"""
from collections import Counter
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from polaris.db.models import Job, Post, RawItem, SearchChunk
from polaris.db.session import session_scope
from polaris.llm.client import LLM, parallel_map
from polaris.search.chunking import Chunk, job_chunks, post_chunks

EMBED_GROUP = 50  # 每个线程一次送多少块（客户端内部再按服务的上限每批 10 条）
EMBED_CONCURRENCY = 4


def build_chunks() -> list[Chunk]:
    with session_scope() as s:
        jobs = s.execute(select(Job).order_by(Job.id)).scalars().all()
        posts = s.execute(select(Post, RawItem.raw_text, RawItem.source)
                          .join(RawItem, RawItem.id == Post.raw_item_id).order_by(Post.id)).all()
        return [c for j in jobs for c in job_chunks(j)] + [c for p, text, src in posts for c in post_chunks(p, text, src)]


def sync_chunks() -> dict[str, int]:
    chunks = build_chunks()
    stats: Counter = Counter()
    with session_scope() as s:
        old = dict(s.execute(select(SearchChunk.key, SearchChunk.content_hash)).all())
        for c in chunks:
            if old.get(c.key) == c.content_hash:
                stats["unchanged"] += 1
                continue
            stats["changed" if c.key in old else "new"] += 1
            row = {"key": c.key, "kind": c.kind, "job_id": c.job_id, "post_id": c.post_id, "companies": list(c.companies),
                   "header": c.header, "body": c.body, "content": c.content, "content_hash": c.content_hash,
                   "published_at": c.published_at, "is_synthetic": c.is_synthetic}
            stmt = insert(SearchChunk).values(row)
            s.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={k: stmt.excluded[k] for k in row if k != "key"}))
        keys = {c.key for c in chunks}
        stale = [k for k in old if k not in keys]
        if stale:
            s.execute(delete(SearchChunk).where(SearchChunk.key.in_(stale)))
        stats["removed"] = len(stale)
    stats["total"] = len(chunks)
    return dict(stats)


def embed_pending(llm: LLM | None = None, limit: int | None = None) -> dict[str, Any]:
    """给没算过向量、或正文变了的块算向量。失败的那组留着，下次再跑会补上。"""
    llm = llm or LLM()
    with session_scope() as s:
        stmt = (select(SearchChunk.id, SearchChunk.content, SearchChunk.content_hash)
                .where(SearchChunk.embedded_hash.is_distinct_from(SearchChunk.content_hash)).order_by(SearchChunk.id))
        rows = s.execute(stmt.limit(limit) if limit else stmt).all()
    groups = [rows[i:i + EMBED_GROUP] for i in range(0, len(rows), EMBED_GROUP)]

    def run(group: list[Any]) -> int:
        vecs = llm.embed([r.content for r in group], purpose="chunk_embed")
        with session_scope() as s:
            for r, v in zip(group, vecs, strict=True):
                s.execute(update(SearchChunk).where(SearchChunk.id == r.id)
                          .values(embedding=v, embedded_hash=r.content_hash, embed_model=llm.settings.embed_model))
        return len(group)

    results = parallel_map(run, groups, EMBED_CONCURRENCY)
    errors = [str(r)[:200] for r in results if isinstance(r, Exception)]
    return {"pending": len(rows), "embedded": sum(r for r in results if isinstance(r, int)), "failed_groups": len(errors),
            "errors": errors[:3]}
