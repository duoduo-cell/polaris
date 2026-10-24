"""跟进卡接口：网页上建卡、加重点岗位、立即检查、改条件、暂停 / 恢复 / 结束、写报告、标记已读；演示接口只在演示模式开放。"""
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from polaris.api.app import app
from polaris.db.models import Job
from polaris.db.session import session_scope

AUTH = {"Authorization": "Bearer test-owner-token"}
NOW = datetime.now(UTC)


def seed():
    with session_scope() as s:
        jobs = []
        for n, days in enumerate((4, 3), 1):
            j = Job(key=f"api-{n}", company="合成科技", company_name="合成科技", title=f"Agent 实习 {n}", city="北京", cities=["北京"],
                    job_type="intern", status="active", directions=["Agent"], days_per_week=days, requirements=["熟悉 Python"],
                    jd_text="JD", first_seen_at=NOW - timedelta(days=5), last_seen_at=NOW, is_synthetic=False)
            s.add(j)
            s.flush()
            jobs.append(j.id)
        return jobs


def test_watch_lifecycle_through_the_api(db, monkeypatch):
    monkeypatch.setattr("polaris.watch.coverage", lambda s, now: [])
    a, b = seed()
    client = TestClient(app)
    created = client.post("/api/me/watches", headers=AUTH, json={
        "title": "北京 Agent 实习", "criteria": {"city": "北京", "job_type": "intern", "direction": "Agent",
                                              "max_days_per_week": 4, "big_only": False},
        "job_ids": [f"J-{a}"], "until": (NOW + timedelta(days=14)).date().isoformat()}).json()
    wid = created["id"]
    assert created["targets"][0]["ref"] == f"J-{a}" and created["criteria_text"] == "北京、实习、Agent、每周最多 4 天"
    assert client.post(f"/api/me/watches/{wid}/targets", headers=AUTH, json={"job": f"J-{b}"}).json()["targets"][1]["ref"] == f"J-{b}"
    assert client.post(f"/api/me/watches/{wid}/report", headers=AUTH).status_code == 409  # 先查过一次才能写报告
    first = client.post(f"/api/me/watches/{wid}/check", headers=AUTH).json()
    assert first["result"]["scope"] == {"active": 2, "baseline": True} and first["watch"]["scope_active"] == 2

    with session_scope() as s:
        s.get(Job, a).days_per_week = 5
    out = client.post(f"/api/me/watches/{wid}/check", headers=AUTH).json()
    assert out["result"]["new_events"] == 1 and out["watch"]["events"][0]["importance"] == "urgent"
    assert out["watch"]["has_unread"] is True
    agenda = client.get("/api/me/agenda", headers=AUTH).json()
    assert any(x["kind"] == "watch" for x in agenda["needs_you"])  # 没看过的紧急变化出现在“今天”页
    assert client.post(f"/api/me/watches/{wid}/events/read", headers=AUTH).json() == {"marked": 1}
    assert client.get(f"/api/me/watches/{wid}", headers=AUTH).json()["has_unread"] is False

    patched = client.patch(f"/api/me/watches/{wid}", headers=AUTH, json={"criteria": {"city": "北京", "max_days_per_week": 5}}).json()
    assert patched["version"] == 2 and patched["scope_active"] is None  # 改条件：版本加一、下次重新建基线
    assert client.patch(f"/api/me/watches/{wid}", headers=AUTH, json={"status": "paused"}).json()["status"] == "paused"
    assert client.post(f"/api/me/watches/{wid}/check", headers=AUTH).status_code == 409
    assert client.patch(f"/api/me/watches/{wid}", headers=AUTH, json={"status": "active"}).json()["status"] == "active"
    report = client.post(f"/api/me/watches/{wid}/report", headers=AUTH)
    assert report.status_code == 200 and report.json()["report"]["markdown"]
    assert client.patch(f"/api/me/watches/{wid}", headers=AUTH, json={"status": "ended"}).json()["status"] == "ended"
    assert client.patch(f"/api/me/watches/{wid}", headers=AUTH, json={"status": "active"}).status_code == 422
    assert client.get("/api/me/watches", headers=AUTH).json()[0]["status"] == "ended"


def test_watch_api_validates_input_and_needs_the_owner_token(db):
    a, _ = seed()
    client = TestClient(app)
    assert client.get("/api/me/watches").status_code == 401
    assert client.post("/api/me/watches", headers=AUTH, json={"title": "x", "cadence_minutes": 5}).status_code == 422
    assert client.post("/api/me/watches", headers=AUTH, json={"title": "x", "job_ids": ["J-999999"]}).status_code == 422
    assert client.post("/api/me/watches", headers=AUTH, json={"title": "x", "job_ids": ["not-a-ref"]}).status_code == 422
    assert client.get("/api/me/watches/999", headers=AUTH).status_code == 404


def test_demo_script_endpoints_are_closed_in_the_personal_instance(db):
    client = TestClient(app)
    assert client.get("/api/me/demo/script", headers=AUTH).json() == {"available": False}
    assert client.post("/api/me/demo/advance", headers=AUTH).status_code == 404
    assert client.post("/api/me/demo/reset", headers=AUTH).status_code == 404
