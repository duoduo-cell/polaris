"""现查（source_open / platform_search）：次数上限、不对外、原链接变化和下线、额度、锁、浏览器没开、R- 卡片。
全部用假的采集器，不碰任何真实平台。"""
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from polaris import live
from polaris.api.app import app
from polaris.collectors import tencent
from polaris.collectors.locks import platform_lock
from polaris.db.models import RawItem
from polaris.db.session import session_scope
from polaris.ingest import RawItemIn, ingest_raw_items
from polaris.mcp_server import build_mcp
from polaris.tools import REGISTRY
from polaris.tools.runtime import ToolRuntime

ME = {"Authorization": "Bearer test-owner-token"}
NOW = datetime(2026, 10, 12, 3, tzinfo=UTC)
OLD = "Agent 研发工程师\n公司：腾讯（CSIG）\n地点：北京\n岗位职责：\n负责企业级 Agent 平台的后端研发和稳定性建设"


def tencent_raw() -> int:
    item = RawItemIn(source="tencent:careers", tier="A", kind="job", url="https://careers.tencent.com/jobdesc.html?postId=1",
                     external_id="1", raw_text=OLD, raw_json={"RecruitPostName": "Agent 研发工程师", "BGName": "CSIG",
                                                             "LocationName": "北京"}, fetched_at=NOW)
    return ingest_raw_items([item]).ids[0]


def test_live_tools_are_capped_per_answer_and_not_exposed(db, monkeypatch):
    rid = tencent_raw()
    same = {"Responsibility": "负责企业级 Agent 平台的后端研发和稳定性建设"}
    monkeypatch.setattr(tencent.TencentCollector, "detail", lambda self, pid: same)
    rt = ToolRuntime()
    outs = [rt.invoke(REGISTRY["source_open"], {"ref": f"R-{rid}"}) for _ in range(4)]
    assert all('"online": true' in o for o in outs[:3]) and "已经现查了 3 次" in outs[3]
    assert "source_open" not in {t.name for t in build_mcp()._tool_manager.list_tools()}
    assert TestClient(app).post("/api/me/tools/platform_search", json={"args": {}}, headers=ME).status_code == 404


def test_open_source_reports_changes_and_stores_new_version(db, monkeypatch):
    rid = tencent_raw()
    monkeypatch.setattr(tencent.TencentCollector, "detail",
                        lambda self, pid: {"Responsibility": "负责企业级 Agent 平台的后端研发和稳定性建设",
                                           "Requirement": "熟悉 LangGraph 等 Agent 框架，有 RAG 落地经验优先"})
    out = live.open_source(f"R-{rid}")
    assert out["online"] is True and out["changed"] is True
    assert any(c.startswith("+") and "LangGraph" in c for c in out["changes"])
    assert out["new_version"] and out["new_version"] != f"R-{rid}"
    with session_scope() as s:
        assert s.get(RawItem, int(out["new_version"][2:])).fetch_method == "live"


def test_open_source_says_when_the_page_is_gone(db, monkeypatch):
    rid = tencent_raw()

    def gone(self, pid):
        raise RuntimeError("腾讯接口返回 Code=404")

    monkeypatch.setattr(tencent.TencentCollector, "detail", gone)
    out = live.open_source(f"R-{rid}")
    assert out["online"] is False and "下线" in out["note"]


def test_login_platforms_respect_budget_lock_and_browser(db, monkeypatch):
    from polaris import browser
    from polaris.collectors import budget

    monkeypatch.setattr(budget, "today_cap", lambda source: (0, "今天已被拦：验证码"))
    with pytest.raises(live.LiveUnavailable, match="不能再查"):
        live.search("xhs", "字节 Agent 面经")
    monkeypatch.setattr(budget, "today_cap", lambda source: (100, "ok"))
    monkeypatch.setattr(budget, "used_today", lambda source: 0)
    monkeypatch.setattr(budget, "hour_left", lambda source: 10)
    monkeypatch.setattr(browser, "is_running", lambda profile="collect": False)
    with pytest.raises(live.LiveUnavailable, match="浏览器没开"):
        live.search("xhs", "字节 Agent 面经")
    with platform_lock("maimai"):
        monkeypatch.setattr(live, "platform_lock", lambda source, timeout: platform_lock(source, timeout=0.5))
        with pytest.raises(live.LiveUnavailable, match="正在被另一个任务使用"):
            live.search("maimai", "Agent 实习")
    out = ToolRuntime().invoke(REGISTRY["platform_search"], {"platform": "xhs", "query": "Agent"})
    assert out.startswith("出错了：现在查不了") and "本地库" in out


def test_search_returns_raw_refs_that_become_cards(db, monkeypatch):
    def fake_collect(self, query):
        yield RawItemIn(source="tencent:careers", tier="A", kind="job", url="https://careers.tencent.com/jobdesc.html?postId=9",
                        external_id="9", raw_text="Agent 实习生\n公司：腾讯\n地点：深圳", raw_json={}, fetched_at=NOW)

    monkeypatch.setattr(tencent.TencentCollector, "collect", fake_collect)
    out = live.search("tencent", "Agent 实习")
    ref = out["items"][0]["id"]
    assert ref.startswith("R-") and out["scope"].startswith("刚刚")
    from polaris.results import entities

    with session_scope() as s:
        card = entities(s, [ref])[0]
    assert card["kind"] == "raw" and card["display_title"] == "Agent 实习生" and card["sources"][0]["url"]


def test_gone_only_when_every_official_listing_missed_daily_snapshots():
    from datetime import timedelta
    from types import SimpleNamespace as V

    from polaris.align.build import status_of

    snap = {"tencent:careers": NOW}
    assert status_of([V(source="tencent:careers", last_seen=NOW - timedelta(days=3))], snap) == "gone"
    assert status_of([V(source="tencent:careers", last_seen=NOW - timedelta(hours=2))], snap) == "active"
    assert status_of([V(source="tencent:careers", last_seen=NOW - timedelta(days=3)),
                      V(source="boss", last_seen=NOW - timedelta(days=3))], snap) == "active"  # Boss 没法判断，不算下架
    assert status_of([V(source="bytedance:campus", last_seen=NOW - timedelta(days=9))], snap) == "active"  # 没做过重采


def test_trend_says_when_data_is_too_thin(db):
    from polaris.tools.jobs import JobsTrendArgs, jobs_trend

    out = jobs_trend(JobsTrendArgs(days=14))
    assert out["snapshot_days"] == 0 and any("至少攒一周" in c for c in out["caveats"])


def test_chat_runtime_reuses_identical_calls_and_caps_repeats(db):
    rt = ToolRuntime(agent="main")
    first = rt.invoke(REGISTRY["posts_search"], {"query": "字节 面经"})
    assert rt.invoke(REGISTRY["posts_search"], {"query": "字节 面经"}) == first and len(rt.calls) == 1  # 一模一样的不重查
    for i in range(10):
        out = rt.invoke(REGISTRY["posts_search"], {"query": f"换个说法 {i}"})
    assert "已经调用 posts_search 8 次" in out
    other = ToolRuntime(agent="bench")  # 评测不受影响
    for i in range(9):
        out = other.invoke(REGISTRY["posts_search"], {"query": f"换个说法 {i}"})
    assert "已经调用" not in out
