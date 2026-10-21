"""岗位清理执行保护：真实隔离库、合成记录、假向量，不接触采集平台。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from polaris.align import build, pruning
from polaris.align.build import run_align
from polaris.db.models import Job, JobFollowup, JobFollowupEvent, Posting, RawItem, Run, SearchChunk
from polaris.db.session import session_scope
from polaris.search import audit, index, maintenance

NOW = datetime(2026, 10, 21, 8, tzinfo=UTC)


class FakeEmbedding:
    settings = SimpleNamespace(embed_model="synthetic-pruning")

    def embed(self, texts, **kwargs):
        return [[1.0] + [0.0] * 1023 for _ in texts]


def add_stale(*, key="synthetic-stale", tracked=False, event_only=False):
    with session_scope() as s:
        job = Job(key=key, title="合成待清理岗位", first_seen_at=NOW, last_seen_at=NOW, is_synthetic=True)
        s.add(job)
        s.flush()
        if tracked:
            s.add(JobFollowup(job_id=job.id, saved=True, note="合成用户笔记"))
        if tracked or event_only:
            s.add(JobFollowupEvent(job_id=job.id, request_key=f"synthetic-{job.id}", before={}, after={},
                                   changes={}, source_text="合成确认记录"))
        return job.id


def add_current():
    with session_scope() as s:
        raw = RawItem(source="tencent:careers", tier="A", kind="job", content_hash="synthetic-current",
                      raw_text="合成岗位", raw_json={}, is_synthetic=True, fetched_at=NOW,
                      first_seen_at=NOW, last_seen_at=NOW)
        s.add(raw)
        s.flush()
        posting = Posting(raw_item_id=raw.id, company_name="腾讯", title="Pruneneedle Agent 实习",
                          city="上海", jd_text="Pruneneedle 可靠工具调用", is_synthetic=True)
        s.add(posting)
        s.flush()
        return posting.id


def assert_preserved(job_id, *, tracked=False, event_only=False):
    with session_scope() as s:
        assert s.get(Job, job_id) is not None
        if tracked:
            assert s.scalar(select(JobFollowup.note).where(JobFollowup.job_id == job_id)) == "合成用户笔记"
        if tracked or event_only:
            assert s.scalar(select(JobFollowupEvent.id).where(JobFollowupEvent.job_id == job_id)) is not None


def test_empty_input_must_not_delete_existing_job_and_user_history(db):
    job_id = add_stale(tracked=True)
    with pytest.raises(RuntimeError, match="prune"):
        run_align()
    assert_preserved(job_id, tracked=True)


@pytest.mark.parametrize("event_only", [False, True])
def test_regroup_cannot_cascade_user_state_and_must_roll_back_alignment(db, event_only):
    posting_id = add_current()
    run_align()
    index.sync_chunks()
    with session_scope() as s:
        old_job = s.scalar(select(Job))
        job_id, old_title = old_job.id, old_job.title
        if not event_only:
            s.add(JobFollowup(job_id=job_id, saved=True, note="合成用户笔记"))
        s.add(JobFollowupEvent(job_id=job_id, request_key="synthetic-regroup", before={}, after={},
                               changes={}, source_text="合成历史"))
        s.execute(update(Posting).where(Posting.id == posting_id).values(title="全新独立岗位名称"))
    with pytest.raises(RuntimeError, match="prune"):
        run_align()
    assert_preserved(job_id, tracked=not event_only, event_only=event_only)
    with session_scope() as s:
        assert list(s.scalars(select(Job.id))) == [job_id]
        assert s.get(Job, job_id).title == old_title
        assert s.get(Posting, posting_id).job_id == job_id
        assert s.scalar(select(SearchChunk.job_id)) == job_id


def test_direct_refresh_cannot_bypass_prune_guard_or_run_downstream(db, monkeypatch):
    add_current()
    stale_id = add_stale()
    monkeypatch.setattr(index, "sync_chunks", lambda: pytest.fail("must stop before chunks"))
    result = index.refresh(graph=False)
    assert set(result) == {"align"} and "prune" in result["align"]["error"]
    assert_preserved(stale_id)
    with session_scope() as s:
        assert s.scalar(select(Posting.job_id)) is None


@pytest.mark.parametrize("change", ["new_job", "edit_job", "followup"])
def test_approval_cannot_authorize_later_unreviewed_changes(db, monkeypatch, change):
    add_current()
    stale_id = add_stale()
    assert audit.enqueue_refresh(allow_prune=True)["queued"]
    late_id = None
    if change == "new_job":
        late_id = add_stale(key="synthetic-late")
    else:
        with session_scope() as s:
            if change == "edit_job":
                s.execute(update(Job).where(Job.id == stale_id).values(title="合成后来修改"))
            else:
                s.add(JobFollowup(job_id=stale_id, saved=True, note="合成用户笔记"))
    monkeypatch.setattr(index, "LLM", lambda: pytest.fail("blocked align must not embed"))
    result = maintenance.run_pending()
    assert result["status"] == "blocked"
    assert result["stats"]["blocked_reason"]
    before = maintenance.status()
    assert maintenance.run_pending()["reason"] == "blocked"
    assert maintenance.status() == before
    assert not maintenance.retry()["queued"]  # 不能借通用 retry 扩大清理许可
    assert_preserved(stale_id)
    if late_id:
        assert_preserved(late_id)
    with session_scope() as s:
        assert s.scalar(select(Posting.job_id)) is None


def test_approved_unchanged_untracked_cleanup_really_finishes_and_is_searchable(db, monkeypatch):
    from polaris.search.retrieve import SearchQuery, keyword, vector

    add_current()
    stale_id = add_stale()
    assert audit.enqueue_refresh(allow_prune=True)["queued"]
    monkeypatch.setattr(index, "LLM", FakeEmbedding)
    assert maintenance.run_pending()["status"] == "ok"
    with session_scope() as s:
        assert s.get(Job, stale_id) is None
        q = SearchQuery("Pruneneedle", scope="jobs", include_synthetic=True)
        assert keyword(s, q, q.text)
        assert vector(s, q, FakeEmbedding().embed([""])[0])
    assert not audit.diagnose()["needs_refresh"]


def test_null_key_is_unmanaged_even_with_empty_input(db):
    job_id = add_stale(key=None, tracked=True)
    out = audit.diagnose()
    assert out["unmanaged_jobs"] == 1 and out["alignment"]["stale_jobs"] == 0
    assert run_align()["stale_removed"] == 0
    assert_preserved(job_id, tracked=True)


def test_empty_input_cannot_be_approved_to_clear_managed_jobs(db):
    job_id = add_stale()
    diagnosis = audit.diagnose()
    assert diagnosis["pruning"]["empty_alignment"]
    assert audit.enqueue_refresh(allow_prune=True)["reason"] == "empty_alignment"
    with pytest.raises(pruning.PruneBlocked, match="empty_alignment"):
        run_align(prune_approval=diagnosis["pruning"]["candidates"])
    assert_preserved(job_id)
    assert maintenance.status()["status"] == "idle"


@pytest.mark.parametrize("event_only", [False, True])
def test_enqueue_never_approves_jobs_with_user_history(db, event_only):
    add_current()
    job_id = add_stale(tracked=not event_only, event_only=event_only)
    assert audit.enqueue_refresh(allow_prune=True)["reason"] == "protected_user_history"
    assert maintenance.status()["status"] == "idle"
    assert_preserved(job_id, tracked=not event_only, event_only=event_only)


def test_changed_posting_membership_invalidates_approval(db):
    posting_id = add_current()
    job_id = add_stale()
    assert audit.enqueue_refresh(allow_prune=True)["queued"]
    with session_scope() as s:
        s.execute(update(Posting).where(Posting.id == posting_id).values(job_id=job_id))
    result = maintenance.run_pending()
    assert result["status"] == "blocked" and result["stats"]["blocked_reason"] == "scope_changed"
    assert_preserved(job_id)
    with session_scope() as s:
        assert s.get(Posting, posting_id).job_id == job_id


def test_new_generation_invalidates_permission_and_keeps_prior_failures(db):
    add_current()
    job_id = add_stale()
    assert audit.enqueue_refresh(allow_prune=True)["queued"]
    with session_scope() as s:
        run = s.scalar(select(Run).where(Run.kind == maintenance.KIND))
        run.stats = {**run.stats, "attempts": {"align": 1, "embed": 2}}
    maintenance.request_refresh("synthetic data after approval")
    before = maintenance.status()
    assert before["params"]["generation"] == 2 and "prune_approval" not in before["params"]
    result = maintenance.run_pending()
    assert result["status"] == "blocked" and result["stats"]["blocked_reason"] == "approval_required"
    assert result["stats"]["attempts"] == {"align": 1, "embed": 2}
    maintenance.request_refresh("synthetic notification while blocked")
    after = maintenance.status()
    assert after["status"] == "blocked" and after["params"]["generation"] == 3
    assert after["stats"]["attempts"] == {"align": 1, "embed": 2}
    assert maintenance.run_pending(now=datetime.now(UTC) + timedelta(days=1))["reason"] == "blocked"
    assert not maintenance.retry()["queued"]
    assert_preserved(job_id)


def test_rediagnosis_can_resume_valid_cleanup_without_resetting_failures(db, monkeypatch):
    add_current()
    job_id = add_stale()
    maintenance.request_refresh("synthetic unapproved cleanup")
    result = maintenance.run_pending()
    assert result["status"] == "blocked"
    with session_scope() as s:
        run = s.get(Run, result["run_id"])
        run.stats = {**run.stats, "attempts": {"align": 1, "embed": 1}}
    assert audit.enqueue_refresh(allow_prune=True)["queued"]
    resumed = maintenance.status()
    assert resumed["status"] == "pending" and "blocked_reason" not in resumed["stats"]
    assert resumed["stats"]["attempts"] == {"align": 1, "embed": 1}
    monkeypatch.setattr(index, "LLM", FakeEmbedding)
    assert maintenance.run_pending()["status"] == "ok"
    # 重复执行与重新通知均不能重复删除、重复生成块或消耗向量服务。
    assert maintenance.run_pending()["reason"] == "ok"
    monkeypatch.setattr(index, "LLM", lambda: pytest.fail("unchanged blocks must not re-embed"))
    maintenance.request_refresh("synthetic repeat after cleanup")
    assert maintenance.run_pending()["status"] == "ok"
    with session_scope() as s:
        assert s.get(Job, job_id) is None
        assert len(list(s.scalars(select(SearchChunk)))) == 1


def test_notification_after_claim_cannot_reuse_captured_old_approval(db, monkeypatch):
    add_current()
    job_id = add_stale()
    assert audit.enqueue_refresh(allow_prune=True)["queued"]
    real_execute = maintenance._execute

    def execute(stage, **kwargs):
        assert stage == "align"
        maintenance.request_refresh("synthetic notification after claim")
        return real_execute(stage, **kwargs)

    monkeypatch.setattr(maintenance, "_execute", execute)
    result = maintenance.run_pending()
    assert result["status"] == "blocked"
    assert result["params"]["generation"] == 2 and "prune_approval" not in result["params"]
    assert result["stats"]["blocked_reason"] == "generation_changed"
    assert result["stats"]["attempts"]["align"] == 0
    assert result["stats"]["stages"] == {}
    assert_preserved(job_id)


def wait_for_blocker(engine, pid):
    deadline = monotonic() + 10
    while monotonic() < deadline:
        with engine.connect() as conn:
            if conn.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}):
                return
        Event().wait(0.01)
    pytest.fail("expected a real PostgreSQL row lock wait")


def test_notification_after_permission_check_waits_then_keeps_new_pending(db, monkeypatch):
    add_current()
    job_id = add_stale()
    assert audit.enqueue_refresh(allow_prune=True)["queued"]
    checked, release, notifying, pid = Event(), Event(), Event(), []
    real_authorize = build.authorize

    def authorize(*args):
        real_authorize(*args)
        checked.set()
        assert release.wait(15)

    def notify():
        with session_scope() as s:
            s.execute(text("SET LOCAL statement_timeout = '15s'"))
            pid.append(s.scalar(text("SELECT pg_backend_pid()")))
            notifying.set()
            maintenance.request_refresh("synthetic notification after guard", session=s)

    monkeypatch.setattr(build, "authorize", authorize)
    monkeypatch.setattr(index, "LLM", lambda: pytest.fail("new generation must not run downstream"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        working = pool.submit(maintenance.run_pending)
        try:
            assert checked.wait(10)
            notification = pool.submit(notify)
            assert notifying.wait(10)
            wait_for_blocker(db, pid[0])
        finally:
            release.set()
        notification.result(timeout=15)
        result = working.result(timeout=15)
    assert result["status"] == "pending" and result["params"]["generation"] == 2
    assert "prune_approval" not in result["params"] and result["stats"]["stages"] == {}
    with session_scope() as s:
        assert s.get(Job, job_id) is None  # 先完成已核验旧代清理，再登记新代；不继承许可。


def test_followup_committed_while_cleanup_waits_is_preserved(db, monkeypatch):
    add_current()
    job_id = add_stale()
    approval = audit.diagnose()["pruning"]["candidates"]
    at_lock, pid = Event(), []
    real_candidates = build.candidates

    def candidates(session, keys, **kwargs):
        pid.append(session.scalar(text("SELECT pg_backend_pid()")))
        at_lock.set()
        return real_candidates(session, keys, **kwargs)

    monkeypatch.setattr(build, "candidates", candidates)
    with ThreadPoolExecutor(max_workers=1) as pool:
        # FK 插入已取 KEY SHARE；清理 FOR UPDATE 必须等保存事务提交。
        with session_scope() as writer:
            writer.add(JobFollowup(job_id=job_id, saved=True, note="合成用户笔记"))
            writer.add(JobFollowupEvent(job_id=job_id, request_key="synthetic-concurrent-save",
                                       before={}, after={}, changes={}, source_text="合成并发确认记录"))
            writer.flush()
            future = pool.submit(run_align, prune_approval=approval)
            assert at_lock.wait(10)
            wait_for_blocker(db, pid[0])
        with pytest.raises(pruning.PruneBlocked, match="user_history"):
            future.result(timeout=15)
    assert_preserved(job_id, tracked=True)


def test_followup_insert_after_guard_cannot_succeed_then_be_cascaded(db, monkeypatch):
    add_current()
    job_id = add_stale()
    approval = audit.diagnose()["pruning"]["candidates"]
    checked, release, inserting, pid = Event(), Event(), Event(), []
    real_authorize = build.authorize

    def authorize(*args):
        real_authorize(*args)
        checked.set()
        assert release.wait(15)

    def save_followup():
        with session_scope() as writer:
            writer.execute(text("SET LOCAL statement_timeout = '15s'"))
            pid.append(writer.scalar(text("SELECT pg_backend_pid()")))
            inserting.set()
            writer.add(JobFollowup(job_id=job_id, saved=True, note="合成后来保存"))

    monkeypatch.setattr(build, "authorize", authorize)
    with ThreadPoolExecutor(max_workers=2) as pool:
        cleaning = pool.submit(run_align, prune_approval=approval)
        try:
            assert checked.wait(10)
            saving = pool.submit(save_followup)
            assert inserting.wait(10)
            wait_for_blocker(db, pid[0])
        finally:
            release.set()
        assert cleaning.result(timeout=15)["stale_removed"] == 1
        # 清理先持有父行锁时，新保存会显式失败；不能声称并发保存总会成功。
        with pytest.raises(IntegrityError):
            saving.result(timeout=15)
