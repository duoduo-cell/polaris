"""你自己用的接口（/api/me）：要口令；记忆能看、能查历史、能忘；推送能给反馈；对话接到主 agent（这里换成假的，不调模型）。"""
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from polaris.api.app import app
from polaris.db.models import Notification
from polaris.db.session import session_scope
from polaris.memory.update import Candidate, Context, apply

client = TestClient(app)
ME = {"Authorization": "Bearer test-owner-token"}
NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)


def remember(attribute: str, value: str) -> None:
    with session_scope() as s:
        apply(Context(s, NOW, f"我{value}"), Candidate(attribute, value, f"用户{attribute}={value}"))


def test_personal_endpoints_need_the_owner_token(db):
    assert client.get("/api/me/memory").status_code == 401
    assert client.get("/api/me/memory", headers={"Authorization": "Bearer test-ingest-token"}).status_code == 401
    assert client.get("/api/me/memory", headers=ME).status_code == 200


def test_memory_card_history_and_forget(db):
    remember("target_city", "北京")
    remember("days_per_week", "4")
    card = client.get("/api/me/memory", params={"on": "2026-10-12"}, headers=ME).json()
    assert {f["value"] for f in card["facts"]} == {"北京", "4"}
    hist = client.get("/api/me/memory/days_per_week/history", headers=ME).json()
    assert hist["versions"][0]["from_text"] == "我4"
    assert client.get("/api/me/memory/nope/history", headers=ME).status_code == 422
    assert client.delete("/api/me/memory/target_city", headers=ME).json() == {"forgotten": 1}
    card = client.get("/api/me/memory", params={"on": "2026-10-12"}, headers=ME).json()
    assert {f["value"] for f in card["facts"]} == {"4"}


def test_notification_feedback(db):
    with session_scope() as s:
        n = Notification(item_key="job:x", kind="job", cite="J-1", title="Agent 实习", company="字节跳动", directions=["Agent"],
                         channel="push", score=0.8, match=0.9, novelty=1, urgency=0, reasons=["方向对得上"], level="normal",
                         created_at=NOW)
        s.add(n)
        s.flush()
        nid = n.id
    rows = client.get("/api/me/notifications", headers=ME).json()
    assert rows[0]["cite"] == "J-1" and rows[0]["feedback"] is None
    assert client.post(f"/api/me/notifications/{nid}/feedback", json={"feedback": "dismissed"}, headers=ME).json() == {"ok": True}
    assert client.get("/api/me/notifications", headers=ME).json()[0]["feedback"] == "dismissed"


def test_chat_goes_to_the_main_agent(db, monkeypatch):
    from polaris.agent import main

    monkeypatch.setattr(main, "chat", lambda message, thread_id: main.Turn(thread_id=thread_id, answer=f"收到：{message}"))
    r = client.post("/api/me/chat", json={"message": "你好", "thread_id": "t1"}, headers=ME).json()
    assert r["answer"] == "收到：你好" and r["thread_id"] == "t1"
