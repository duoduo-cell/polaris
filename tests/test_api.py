"""REST 接口。需要 docker compose up -d db。"""
from fastapi.testclient import TestClient

from polaris.api.app import app
from polaris.llm.recorder import DbRecorder

client = TestClient(app)
AUTH = {"Authorization": "Bearer test-ingest-token"}


def test_health_shows_models_but_no_secrets(db):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] is True and body["config"]["models"]["chat"] == "test-model"
    assert "sk-test" not in r.text and "test-ingest-token" not in r.text


def test_ingest_requires_token(db):
    payload = {"items": [{"source": "manual", "raw_text": "岗位 A"}]}
    assert client.post("/api/ingest/raw-items", json=payload).status_code == 401
    assert client.post("/api/ingest/raw-items", json=payload,
                       headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_ingest_then_list_and_fetch(db):
    long_text = "大模型评测实习生。" * 100
    payload = {"items": [{"source": "manual", "raw_text": "岗位 A", "url": "https://jobs.example.com/a"},
                         {"source": "manual", "raw_text": long_text, "tier": "B"}]}
    r = client.post("/api/ingest/raw-items", json=payload, headers=AUTH)
    assert r.status_code == 200 and r.json()["inserted"] == 2
    again = client.post("/api/ingest/raw-items", json=payload, headers=AUTH).json()
    assert again["inserted"] == 0 and again["duplicates"] == 2

    listed = client.get("/api/raw-items", params={"tier": "B"}).json()
    assert len(listed) == 1 and listed[0]["raw_text"].endswith("…")  # 列表里截断
    full = client.get(f"/api/raw-items/{listed[0]['id']}").json()
    assert full["raw_text"] == long_text
    assert client.get("/api/raw-items/999999").status_code == 404


def test_ingest_rejects_bad_items(db):
    r = client.post("/api/ingest/raw-items", json={"items": [{"source": "manual", "raw_text": ""}]}, headers=AUTH)
    assert r.status_code == 422


def test_llm_calls_list_and_detail(db):
    rec = DbRecorder()
    call_id = rec.record({"purpose": "extract_job", "provider": "p", "model": "m", "endpoint": "chat",
                          "request": {"messages": [{"role": "user", "content": "hi"}]},
                          "response": {"choices": []}, "latency_ms": 12, "prompt_tokens": 3,
                          "completion_tokens": 1})
    listed = client.get("/api/llm-calls", params={"purpose": "extract_job"}).json()
    assert [c["id"] for c in listed] == [call_id] and "request" not in listed[0]
    detail = client.get(f"/api/llm-calls/{call_id}").json()
    assert detail["request"]["messages"][0]["content"] == "hi" and detail["latency_ms"] == 12
