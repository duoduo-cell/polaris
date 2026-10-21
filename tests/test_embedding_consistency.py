"""向量服务返回期间发生数据变化：旧结果不能覆盖已更新的检索状态。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

from sqlalchemy import delete, select, update

from polaris.db.models import SearchChunk
from polaris.db.session import session_scope
from polaris.search import index
from polaris.search.retrieve import SearchQuery, vector

OLD_VECTOR = [1.0] + [0.0] * 1023
NEW_VECTOR = [0.0, 1.0] + [0.0] * 1022


class ControlledEmbedding:
    settings = SimpleNamespace(embed_model="synthetic-consistency")

    def __init__(self, result, during_request=lambda: None):
        self.result = result
        self.during_request = during_request
        self.requests = []

    def embed(self, texts, **kwargs):
        self.requests.append(texts)
        self.during_request()
        return [self.result for _ in texts]


def seed_chunk():
    with session_scope() as s:
        chunk = SearchChunk(key="consistency", kind="post", companies=[], header="合成",
                            body="old", content="old", content_hash="old-hash", is_synthetic=True)
        s.add(chunk)
        s.flush()
        return chunk.id


def change_content(chunk_id):
    with session_scope() as s:
        s.execute(update(SearchChunk).where(SearchChunk.id == chunk_id)
                  .values(body="new", content="new", content_hash="new-hash"))


def test_late_old_result_cannot_replace_new_content_embedding(db):
    chunk_id = seed_chunk()
    newer = ControlledEmbedding(NEW_VECTOR)

    def complete_newer_request():
        change_content(chunk_id)
        assert index.embed_pending(newer)["embedded"] == 1

    older = ControlledEmbedding(OLD_VECTOR, complete_newer_request)
    out = index.embed_pending(older)

    assert older.requests == [["old"]] and newer.requests == [["new"]]
    with session_scope() as s:
        chunk = s.get(SearchChunk, chunk_id)
        assert chunk.embedded_hash == chunk.content_hash == "new-hash"
        assert list(chunk.embedding) == NEW_VECTOR
        assert vector(s, SearchQuery("new", include_synthetic=True), NEW_VECTOR) == [(chunk_id, 1.0)]
    assert out["pending"] == 1 and out["embedded"] == 0 and out["remaining"] == 0


def test_changed_content_without_new_embedding_stays_pending_until_next_pass(db):
    chunk_id = seed_chunk()
    out = index.embed_pending(ControlledEmbedding(OLD_VECTOR, lambda: change_content(chunk_id)))

    with session_scope() as s:
        chunk = s.get(SearchChunk, chunk_id)
        assert chunk.embedding is None and chunk.embedded_hash is None
        assert vector(s, SearchQuery("new", include_synthetic=True), OLD_VECTOR) == []
    assert out["embedded"] == 0 and out["remaining"] == 1

    newer = ControlledEmbedding(NEW_VECTOR)
    assert index.embed_pending(newer)["embedded"] == 1
    assert newer.requests == [["new"]]
    with session_scope() as s:
        assert vector(s, SearchQuery("new", include_synthetic=True), NEW_VECTOR) == [(chunk_id, 1.0)]


def test_late_duplicate_cannot_replace_completed_embedding_of_same_content(db):
    chunk_id = seed_chunk()
    newer = ControlledEmbedding(NEW_VECTOR)

    def complete_duplicate():
        assert index.embed_pending(newer)["embedded"] == 1

    out = index.embed_pending(ControlledEmbedding(OLD_VECTOR, complete_duplicate))
    with session_scope() as s:
        assert list(s.get(SearchChunk, chunk_id).embedding) == NEW_VECTOR
    assert out["embedded"] == 0 and out["remaining"] == 0


def test_deleted_during_embedding_is_not_counted_as_saved(db):
    chunk_id = seed_chunk()

    def delete_chunk():
        with session_scope() as s:
            s.execute(delete(SearchChunk).where(SearchChunk.id == chunk_id))

    out = index.embed_pending(ControlledEmbedding(OLD_VECTOR, delete_chunk))
    assert out["pending"] == 1 and out["embedded"] == 0 and out["remaining"] == 0
    with session_scope() as s:
        assert s.scalar(select(SearchChunk.id)) is None


def test_two_real_workers_only_one_commits_same_content_embedding(db):
    chunk_id = seed_chunk()
    both_requested = Barrier(2)
    first = ControlledEmbedding(OLD_VECTOR, lambda: both_requested.wait(timeout=10))
    second = ControlledEmbedding(NEW_VECTOR, lambda: both_requested.wait(timeout=10))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(index.embed_pending, fake) for fake in (first, second)]
        results = [future.result(timeout=20) for future in futures]

    assert sorted(result["embedded"] for result in results) == [0, 1]
    assert all(result["remaining"] == result["failed_groups"] == 0 for result in results)
    assert first.requests == second.requests == [["old"]]
    with session_scope() as s:
        chunk = s.get(SearchChunk, chunk_id)
        assert chunk.embedded_hash == chunk.content_hash == "old-hash"
        assert list(chunk.embedding) in (OLD_VECTOR, NEW_VECTOR)


def test_changed_row_does_not_block_unchanged_batch_members(db):
    changed_id = seed_chunk()
    with session_scope() as s:
        s.add(SearchChunk(key="stable", kind="post", companies=[], header="合成", body="stable",
                          content="stable", content_hash="stable-hash", is_synthetic=True))
    out = index.embed_pending(ControlledEmbedding(OLD_VECTOR, lambda: change_content(changed_id)))
    assert out["pending"] == 2 and out["embedded"] == 1 and out["remaining"] == 1
    newer = ControlledEmbedding(NEW_VECTOR)
    out = index.embed_pending(newer)
    assert out["pending"] == out["embedded"] == 1 and out["remaining"] == 0
    assert newer.requests == [["new"]]
