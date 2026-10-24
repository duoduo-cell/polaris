"""跟进卡的事件回放（docs/design/WATCH.md 第 9 节）：每种变化该产生什么事件、什么级别、提醒几次。"""
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from polaris import watch as watch_module
from polaris.db.models import Job, PeerProfile, Post, RawItem, Watch, WatchEvent, WatchReport
from polaris.db.session import session_scope
from polaris.ingest import content_hash
from polaris.watch import check, create, run_due, update
from polaris.watch_report import allowed_refs, rule_markdown, write_report

NOW = datetime(2026, 10, 25, 4, 0, tzinfo=UTC)  # 北京时间 12:00
CRITERIA = {"city": "北京", "job_type": "intern", "direction": "Agent", "max_days_per_week": 4, "big_only": False}


@pytest.fixture(autouse=True)
def _no_coverage(monkeypatch):
    """岗位相关的回放不掺来源覆盖；覆盖单独测。"""
    monkeypatch.setattr(watch_module, "coverage", lambda s, now: [])


def job(s, key, *, days=4, status="active", first_seen=NOW - timedelta(days=10), company="合成科技", city="北京", **kw):
    j = Job(key=key, company=company, company_name=company, title=f"Agent 开发实习 {key}", city=city, cities=[city],
            job_type="intern", status=status, directions=["Agent"], days_per_week=days, requirements=["熟悉 Python"],
            jd_text=f"JD {key}", first_seen_at=first_seen, last_seen_at=first_seen, is_synthetic=False, **kw)
    s.add(j)
    s.flush()
    return j


def events(watch_id, *, include_quiet=True):
    with session_scope() as s:
        rows = s.scalars(select(WatchEvent).where(WatchEvent.watch_id == watch_id).order_by(WatchEvent.id)).all()
        return [(e.kind, e.importance, e.ref) for e in rows if include_quiet or e.importance != "quiet"]


def run_check(watch_id, now, notified=None):
    sink = notified if notified is not None else []
    with session_scope() as s:
        return check(s, s.get(Watch, watch_id), now, notify_fn=lambda title, text: sink.append(text))


def setup_watch(targets=("a",), **kw):
    with session_scope() as s:
        jobs = {k: job(s, k) for k in ("a", "b", "c")}
        w = create(s, title="北京 Agent 日常实习", criteria=kw.pop("criteria", CRITERIA),
                   job_ids=[jobs[k].id for k in targets], now=NOW, **kw)
        return w.id, {k: j.id for k, j in jobs.items()}


def test_first_check_only_builds_baseline_and_quiet_rechecks_add_nothing(db):
    wid, _ = setup_watch()
    out = run_check(wid, NOW)
    assert out["scope"] == {"active": 3, "baseline": True} and out["new_events"] == 0
    assert run_check(wid, NOW + timedelta(hours=1))["new_events"] == 0
    with session_scope() as s:
        w = s.get(Watch, wid)
        assert w.last_check_at == NOW + timedelta(hours=1) and w.next_check_at == NOW + timedelta(hours=2)


def test_target_days_change_that_breaks_the_condition_is_urgent_and_notified_once(db):
    wid, ids = setup_watch()
    run_check(wid, NOW)
    with session_scope() as s:
        s.get(Job, ids["a"]).days_per_week = 5
    notified = []
    assert run_check(wid, NOW + timedelta(hours=1), notified)["new_events"] == 1
    assert events(wid) == [("job_changed", "urgent", f"J-{ids['a']}")]
    with session_scope() as s:
        e = s.scalar(select(WatchEvent).where(WatchEvent.watch_id == wid))
        assert e.detail["fields"] == [{"field": "days_per_week", "label": "每周天数", "before": 4, "after": 5, "conflict": True}]
        assert e.detail["label"] == "合成科技 · Agent 开发实习 a"
    assert len(notified) == 1 and "和你的条件冲突了" in notified[0]
    assert run_check(wid, NOW + timedelta(hours=2), notified)["new_events"] == 0  # 重复检查、重启重跑：不多记、不多提醒
    assert len(events(wid)) == 1 and len(notified) == 1


def test_requirement_list_change_records_only_added_and_removed_items(db):
    wid, ids = setup_watch()
    run_check(wid, NOW)
    with session_scope() as s:
        s.get(Job, ids["a"]).requirements = ["熟悉 Python", "有评测经验"]
    run_check(wid, NOW + timedelta(hours=1))
    with session_scope() as s:
        field = s.scalar(select(WatchEvent).where(WatchEvent.watch_id == wid)).detail["fields"][0]
    assert field["added"] == ["有评测经验"] and field["removed"] == []


def test_non_conflicting_change_is_brief_and_jd_wording_only_is_quiet(db):
    wid, ids = setup_watch()
    run_check(wid, NOW)
    with session_scope() as s:
        s.get(Job, ids["a"]).days_per_week = 3
    run_check(wid, NOW + timedelta(hours=1))
    with session_scope() as s:
        s.get(Job, ids["a"]).jd_text = "JD 改了措辞"
    run_check(wid, NOW + timedelta(hours=2))
    assert events(wid) == [("job_changed", "brief", f"J-{ids['a']}"), ("job_changed", "quiet", f"J-{ids['a']}")]


def test_closed_target_is_urgent_reopen_is_brief_and_not_reported_twice_by_scope(db):
    wid, ids = setup_watch()
    run_check(wid, NOW)
    with session_scope() as s:
        s.get(Job, ids["a"]).status = "gone"
    run_check(wid, NOW + timedelta(hours=1))
    with session_scope() as s:
        j = s.get(Job, ids["a"])
        j.status, j.last_seen_at = "active", NOW + timedelta(hours=2)
    run_check(wid, NOW + timedelta(hours=3))
    # 范围视角不再为同一个岗位记“下架”“进入范围”
    assert events(wid) == [("job_closed", "urgent", f"J-{ids['a']}"), ("job_reopened", "brief", f"J-{ids['a']}")]


def test_target_removed_from_library_is_not_called_closed(db):
    wid, ids = setup_watch()
    run_check(wid, NOW)
    with session_scope() as s:
        s.delete(s.get(Job, ids["a"]))
    run_check(wid, NOW + timedelta(hours=1))
    run_check(wid, NOW + timedelta(hours=2))
    assert [k for k, _, _ in events(wid)] == ["job_removed"]


def test_new_jobs_after_baseline_respect_the_conditions(db):
    wid, _ = setup_watch()
    run_check(wid, NOW)
    with session_scope() as s:
        unknown = job(s, "new-unknown-days", days=None, first_seen=NOW + timedelta(minutes=30))
        job(s, "new-five-days", days=5, first_seen=NOW + timedelta(minutes=30))  # 超出每周 4 天：不在范围
        job(s, "new-shanghai", city="上海", first_seen=NOW + timedelta(minutes=30))  # 城市不符：不在范围
        old = job(s, "old-entered", days=None, first_seen=NOW - timedelta(days=30), status="active")
        ids = (unknown.id, old.id)
    run_check(wid, NOW + timedelta(hours=1))
    got = events(wid)
    assert ("new_job", "brief", f"J-{ids[0]}") in got  # 天数没写：进简报，不当成符合
    assert ("scope_entered", "quiet", f"J-{ids[1]}") in got  # 开卡前就有：不算新增
    assert len(got) == 2


def test_criteria_change_bumps_version_rebaselines_and_drops_old_pending_alerts(db):
    wid, ids = setup_watch()
    run_check(wid, NOW)
    with session_scope() as s:
        s.get(Job, ids["a"]).days_per_week = 5
        w = s.get(Watch, wid)
        w.notify = "brief"  # 先只进报告，不发通知
    run_check(wid, NOW + timedelta(hours=1))
    with session_scope() as s:
        w = s.get(Watch, wid)
        update(s, w, {"criteria": {**CRITERIA, "max_days_per_week": 5}, "notify": "desktop"}, NOW + timedelta(hours=2))
        assert w.version == 2 and w.state == {}
    notified = []
    out = run_check(wid, NOW + timedelta(hours=3), notified)
    assert out["scope"]["baseline"] is True and notified == []  # 旧条件下的紧急事件不再提醒；新范围不刷屏


def test_pause_end_and_expiry(db):
    wid, _ = setup_watch(until=date(2026, 10, 26))
    with session_scope() as s:
        update(s, s.get(Watch, wid), {"status": "paused"})
    assert run_check(wid, NOW) == {"skipped": "跟进卡是paused状态"}
    with session_scope() as s:
        w = s.get(Watch, wid)
        update(s, w, {"status": "active"}, NOW)
        assert w.next_check_at == NOW
    run_due(NOW, echo=lambda _: None, notify_fn=lambda *_: None, llm=FakeLLM("## 发生了什么\n- 无"))
    late = NOW + timedelta(days=2)
    stats = run_due(late, echo=lambda _: None, notify_fn=lambda *_: None, llm=FakeLLM("## 发生了什么\n- 到期"))
    assert stats["ended"] == 1 and stats["reports"] == 1
    with session_scope() as s:
        assert s.get(Watch, wid).status == "ended"
        with pytest.raises(watch_module.WatchError):
            update(s, s.get(Watch, wid), {"status": "active"})


def test_materials_and_peers_in_scope_become_brief_events(db):
    wid, ids = setup_watch()
    run_check(wid, NOW)
    with session_scope() as s:
        text = "合成科技 Agent 实习一面面经：问了工具失败怎么恢复"
        raw = RawItem(source="xhs", tier="B", kind="post", raw_text=text, content_hash=content_hash(text), fetched_at=NOW)
        s.add(raw)
        s.flush()
        post = Post(raw_item_id=raw.id, post_type="面经", companies=["合成科技"], big_companies=["合成科技"],
                    directions=["Agent"], summary=text, published_at=NOW - timedelta(days=1), interview_questions=["工具失败怎么恢复"],
                    created_at=NOW + timedelta(minutes=30))
        peer = PeerProfile(raw_item_id=raw.id, source="xhs", published_at=NOW - timedelta(days=1), who="作者", stage="本科",
                           school="双非", big_company="合成科技", target_role="Agent 开发实习", job_type="日常实习",
                           direction="Agent", outcome="拿到offer", evidence="本科双非拿到 offer", prompt_version="t",
                           created_at=NOW + timedelta(minutes=30))
        s.add_all([post, peer])
        s.flush()
        refs = (f"P-{post.id}", f"R-{raw.id}")
    run_check(wid, NOW + timedelta(hours=1))
    run_check(wid, NOW + timedelta(hours=2))
    assert events(wid) == [("new_material", "brief", refs[0]), ("new_peer", "brief", refs[1])]


def test_material_saved_before_the_card_was_opened_is_not_new(db):
    with session_scope() as s:
        text = "合成科技 Agent 面经（开卡前就入库了）"
        raw = RawItem(source="xhs", tier="B", kind="post", raw_text=text, content_hash=content_hash(text), fetched_at=NOW)
        s.add(raw)
        s.flush()
        s.add(Post(raw_item_id=raw.id, post_type="面经", companies=["合成科技"], big_companies=["合成科技"], directions=["Agent"],
                   summary=text, published_at=NOW - timedelta(days=1), created_at=NOW - timedelta(hours=2)))
    wid, _ = setup_watch()
    run_check(wid, NOW)
    run_check(wid, NOW + timedelta(hours=1))
    assert events(wid) == []


def test_coverage_gaps_are_reported_once_per_source_per_day(db, monkeypatch):
    monkeypatch.setattr(watch_module, "coverage", lambda s, now: [
        {"source": "tencent", "label": "官网 tencent", "last_ok": None, "problem": "超过 36 小时没有成功重采"}])
    wid, _ = setup_watch()
    run_check(wid, NOW)
    run_check(wid, NOW + timedelta(hours=1))
    run_check(wid, NOW + timedelta(days=1))
    assert [k for k, _, _ in events(wid)] == ["source_gap", "source_gap"]


class FakeLLM:
    def __init__(self, text, fail=False):
        self.text, self.fail, self.calls = text, fail, 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("模型连不上")
        return SimpleNamespace(content=self.text, call_id=None)


def test_report_uses_model_text_only_when_its_refs_come_from_the_facts(db):
    wid, ids = setup_watch()
    run_check(wid, NOW)
    with session_scope() as s:
        s.get(Job, ids["a"]).days_per_week = 5
    run_check(wid, NOW + timedelta(hours=1))
    with session_scope() as s:
        w = s.get(Watch, wid)
        good = write_report(s, w, NOW + timedelta(hours=2), llm=FakeLLM(f"## 发生了什么\n- J-{ids['a']} 改成每周 5 天"))
        assert good.fallback is None and f"J-{ids['a']}" in good.markdown
        assert f"J-{ids['a']}" in allowed_refs(good.facts)
        stray = write_report(s, w, NOW + timedelta(hours=3), llm=FakeLLM("## 发生了什么\n- J-999999 有新岗位"))
        assert stray.fallback.startswith("refs_outside_facts") and "J-999999" not in stray.markdown
        broken = write_report(s, w, NOW + timedelta(hours=4), llm=FakeLLM("", fail=True))
        assert broken.fallback == "model_error:RuntimeError" and "## 你还差什么" in broken.markdown


def test_rule_report_states_conflicts_unknowns_and_missing_coverage(db):
    wid, ids = setup_watch()
    run_check(wid, NOW)
    with session_scope() as s:
        s.get(Job, ids["a"]).days_per_week = 5
    run_check(wid, NOW + timedelta(hours=1))
    with session_scope() as s:
        report = write_report(s, s.get(Watch, wid), NOW + timedelta(hours=2), use_model=False)
        f = report.facts
    assert report.fallback == "rule_only"
    assert f["event_counts"] == {"urgent": 1} and f["scope"]["active_jobs"] == 2  # a 改成 5 天后离开范围
    text = rule_markdown({**f, "coverage_gaps": [{"label": "官网 tencent", "problem": "超过 36 小时没有成功重采"}]})
    assert "【紧急】" in text and "官网 tencent" in text


def test_daily_model_budget_falls_back_to_rules(db, monkeypatch):
    from polaris import watch_report

    monkeypatch.setattr(watch_report, "DAILY_LLM_REPORTS", 2)
    wid, _ = setup_watch()
    run_check(wid, NOW)
    llm = FakeLLM("## 发生了什么\n- 无")
    with session_scope() as s:
        w = s.get(Watch, wid)
        reports = [write_report(s, w, NOW + timedelta(minutes=i), llm=llm) for i in range(3)]
        assert s.scalar(select(WatchReport.id).order_by(WatchReport.id.desc()).limit(1)) == reports[-1].id
    assert [r.fallback for r in reports] == [None, None, "daily_model_budget"] and llm.calls == 2
