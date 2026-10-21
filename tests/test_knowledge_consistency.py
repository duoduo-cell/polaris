"""派生知识的读取到写回竞争：真实隔离 PostgreSQL，事件控制顺序，不调用平台／模型。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event, current_thread

import pytest
from sqlalchemy import select, update

from polaris.align import build
from polaris.db.models import InterviewQuestion, Job, Post, Posting, RawItem, SearchChunk
from polaris.db.session import session_scope
from polaris.interviews import sync_questions
from polaris.search import index
from polaris.search.locking import KnowledgeWriteBusy, acquire_knowledge_write
from polaris.search.retrieve import SearchQuery, keyword

NOW = datetime(2026, 10, 21, 8, tzinfo=UTC)


def add_post(s, suffix, body):
    raw = RawItem(source="synthetic:consistency", tier="A", kind="post", content_hash=suffix,
                  raw_text=body, raw_json={}, is_synthetic=True, fetched_at=NOW)
    s.add(raw)
    s.flush()
    post = Post(raw_item_id=raw.id, post_type="面经", summary="合成并发案例",
                interview_questions=["如何处理可靠调用？"], published_at=NOW)
    s.add(post)
    s.flush()
    return post.id, raw.id


def call_or_busy(fn):
    try:
        fn()
        return False
    except KnowledgeWriteBusy:
        return True


@pytest.mark.parametrize("newer_entry", ["full", "post"])
def test_older_full_snapshot_cannot_erase_newer_full_or_post_sync(db, monkeypatch, newer_entry):
    with session_scope() as s:
        post_id, raw_id = add_post(s, "original", "Oldneedle 合成旧正文")
    index.sync_chunks()
    captured, release = Event(), Event()
    real_build = index.build_chunks

    def capture_snapshot(*args, **kwargs):
        chunks = real_build(*args, **kwargs)
        if current_thread().name.startswith("older-refresh"):
            captured.set()
            assert release.wait(15), "controller did not release old snapshot"
        return chunks

    monkeypatch.setattr(index, "build_chunks", capture_snapshot)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="older-refresh") as pool:
        older = pool.submit(index.sync_chunks)
        try:
            assert captured.wait(15), "old snapshot not reached"
            with session_scope() as s:
                s.execute(update(RawItem).where(RawItem.id == raw_id).values(raw_text="Newneedle 合成新正文"))
                new_post_id, _ = add_post(s, "new-post", "Freshneedle 合成新增面经")

            def newer():
                if newer_entry == "full":
                    index.sync_chunks()
                else:
                    with session_scope() as s:
                        index.sync_post_chunks(s, [post_id, new_post_id])

            busy = call_or_busy(newer)
        finally:
            release.set()
        older.result(timeout=15)
    if busy:
        newer()  # 竞争结束后显式重试，必须重新读，不能复用老计算结果。
    with session_scope() as s:
        rows = s.scalars(select(SearchChunk).where(SearchChunk.kind == "post")).all()
        assert {c.post_id for c in rows} == {post_id, new_post_id}
        assert "Newneedle" in next(c.body for c in rows if c.post_id == post_id)
        hits = keyword(s, SearchQuery("Freshneedle", include_synthetic=True), "Freshneedle")
        assert any(cid == c.id for cid, _ in hits for c in rows if c.post_id == new_post_id)


def test_older_alignment_cannot_overwrite_new_jobs_and_posting_links(db, monkeypatch):
    with session_scope() as s:
        raw = RawItem(source="tencent:careers", tier="A", kind="job", content_hash="original-job",
                      raw_text="合成", raw_json={}, is_synthetic=True, fetched_at=NOW,
                      first_seen_at=NOW, last_seen_at=NOW)
        s.add(raw)
        s.flush()
        posting = Posting(raw_item_id=raw.id, company_name="腾讯", title="Agent开发实习生",
                          jd_text="Oldneedle", city="上海", is_synthetic=True)
        s.add(posting)
        s.flush()
        posting_id = posting.id
    build.run_align()
    captured, release = Event(), Event()
    real_load = build.load_views

    def capture_snapshot(*args, **kwargs):
        views = real_load(*args, **kwargs)
        if current_thread().name.startswith("older-align"):
            captured.set()
            assert release.wait(15)
        return views

    monkeypatch.setattr(build, "load_views", capture_snapshot)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="older-align") as pool:
        older = pool.submit(build.run_align)
        try:
            assert captured.wait(15)
            with session_scope() as s:
                s.execute(update(Posting).where(Posting.id == posting_id).values(jd_text="Newneedle"))
                raw = RawItem(source="tencent:careers", tier="A", kind="job", content_hash="fresh-job",
                              raw_text="合成", raw_json={}, is_synthetic=True, fetched_at=NOW,
                              first_seen_at=NOW, last_seen_at=NOW)
                s.add(raw)
                s.flush()
                s.add(Posting(raw_item_id=raw.id, company_name="腾讯", title="独立数据库研发实习生",
                              jd_text="Freshneedle", city="深圳", is_synthetic=True))
            busy = call_or_busy(build.run_align)
        finally:
            release.set()
        older.result(timeout=15)
    if busy:
        build.run_align()
    with session_scope() as s:
        jobs = s.scalars(select(Job)).all()
        assert len(jobs) == 2
        assert {j.jd_text for j in jobs} == {"Newneedle", "Freshneedle"}
        assert {p.job_id for p in s.scalars(select(Posting))} == {j.id for j in jobs}


def test_full_sync_failure_rolls_back_questions_profiles_and_chunks_together(db, monkeypatch):
    with session_scope() as s:
        post_id, _ = add_post(s, "rollback-post", "合成面经 如何处理可靠调用？")
    index.sync_chunks()
    with session_scope() as s:
        before_profile = s.get(Post, post_id).evidence_profile
        before_questions = dict(s.execute(select(InterviewQuestion.key, InterviewQuestion.question)).all())
        before_chunks = dict(s.execute(select(SearchChunk.key, SearchChunk.content)).all())
        s.get(Post, post_id).interview_questions = ["如何处理并发任务？", "如何检测失败？"]
    real_sync = index._sync_chunks

    def fail_after_writes(*args):
        real_sync(*args)
        raise RuntimeError("controlled failure after chunk writes")

    monkeypatch.setattr(index, "_sync_chunks", fail_after_writes)
    with pytest.raises(RuntimeError, match="controlled failure"):
        index.sync_chunks()
    with session_scope() as s:
        assert dict(s.execute(select(InterviewQuestion.key, InterviewQuestion.question)).all()) == before_questions
        assert s.get(Post, post_id).evidence_profile == before_profile
        assert dict(s.execute(select(SearchChunk.key, SearchChunk.content)).all()) == before_chunks
    monkeypatch.setattr(index, "_sync_chunks", real_sync)
    assert index.sync_chunks()["changed"] > 0  # 回滚后锁释放，下一次可重新完成。


def test_post_sync_refreshes_objects_previously_read_by_caller(db):
    with session_scope() as s:
        post_id, raw_id = add_post(s, "stale-session", "Oldneedle 合成旧正文")
    index.sync_chunks()
    with session_scope() as caller:
        cached = caller.get(RawItem, raw_id)
        post = caller.get(Post, post_id)
        with session_scope() as writer:
            writer.execute(update(RawItem).where(RawItem.id == raw_id).values(raw_text="Newneedle 合成新正文"))
        index.sync_post_chunks(caller, [post_id])
        assert cached.raw_text == "Newneedle 合成新正文" and post.id == post_id
        assert "Newneedle" in caller.scalar(select(SearchChunk.body).where(
            SearchChunk.post_id == post_id, SearchChunk.kind == "post"))


@pytest.mark.parametrize("entry", ["align", "chunks", "post", "questions"])
def test_all_derived_entries_are_busy_before_read_or_write(db, monkeypatch, entry):
    with session_scope() as s:
        post_id, _ = add_post(s, "busy-post", "Busyneedle 合成正文")
    # 即使持锁方来自指定帖子／题目入口，同一事务锁也应保护对齐和全量同步。
    with session_scope() as owner:
        index.sync_post_chunks(owner, [post_id])
        if entry == "align":
            monkeypatch.setattr(build, "load_views", lambda *a: pytest.fail("read before lock"))
        elif entry == "chunks":
            monkeypatch.setattr(index, "build_chunks", lambda *a: pytest.fail("read before lock"))
        with pytest.raises(KnowledgeWriteBusy), session_scope() as contender:
            if entry == "align":
                build.run_align()
            elif entry == "chunks":
                index.sync_chunks()
            elif entry == "post":
                index.sync_post_chunks(contender, [post_id])
            else:
                sync_questions(contender, [post_id])
    with session_scope() as next_writer:
        acquire_knowledge_write(next_writer)  # owner 提交后可取得，不遗留会话锁。


def test_populate_existing_preserves_callers_uncommitted_edits(db):
    with session_scope() as s:
        post_id, raw_id = add_post(s, "dirty-post", "Oldneedle 原文")
    with session_scope() as caller:
        raw, post = caller.get(RawItem, raw_id), caller.get(Post, post_id)
        raw.raw_text = "Dirtyneedle 合法未提交面经 如何处理并发任务？"
        post.interview_questions = ["如何处理并发任务？"]
        index.sync_post_chunks(caller, [post_id])
        assert "Dirtyneedle" in raw.raw_text
        assert post.interview_questions == ["如何处理并发任务？"]
    with session_scope() as s:
        assert "Dirtyneedle" in s.get(RawItem, raw_id).raw_text
        assert s.scalar(select(InterviewQuestion.question)) == "如何处理并发任务？"
        assert "Dirtyneedle" in s.scalar(select(SearchChunk.body).where(SearchChunk.kind == "post"))


def test_busy_does_not_flush_callers_pending_changes(db):
    with session_scope() as s:
        post_id, raw_id = add_post(s, "no-flush", "Oldneedle 原文")
    with session_scope() as owner:
        acquire_knowledge_write(owner)
        with session_scope() as caller:
            caller.get(RawItem, raw_id).raw_text = "MustNotSave 未授权写回"
            with pytest.raises(KnowledgeWriteBusy):
                index.sync_post_chunks(caller, [post_id])
            # 锁忙不自动 rollback caller 的其他工作，但本入口不能先 flush 它们。
            assert caller.dirty
            caller.rollback()
    with session_scope() as s:
        assert s.get(RawItem, raw_id).raw_text == "Oldneedle 原文"


@pytest.mark.parametrize("stage", ["align", "chunks"])
def test_direct_refresh_stops_after_busy_upstream(db, monkeypatch, stage):
    if stage == "chunks":
        monkeypatch.setattr(build, "run_align", lambda: {})
    monkeypatch.setattr(index, "embed_pending", lambda: pytest.fail("embed after busy upstream"))
    with session_scope() as owner:
        acquire_knowledge_write(owner)
        out = index.refresh(graph=False)
    assert out[stage]["retryable"] is True and "KnowledgeWriteBusy" in out[stage]["error"]
    assert "embed" not in out
