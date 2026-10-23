"""通过有限ASGI内存请求检查实际鉴权依赖；无Polaris lifespan／服务／数据库或模型。"""
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from polaris.api import personal
from polaris.config import Settings


@pytest.mark.parametrize("demo,expected,configured,provided,status", [
    (True, "synthetic_demo", "synthetic-token", "synthetic-token", 200),
    (False, "personal", "synthetic-token", "synthetic-token", 200),
    (True, "personal", "synthetic-token", "synthetic-token", 409),
    (False, "synthetic_demo", "synthetic-token", "synthetic-token", 409),
    (False, "unknown", "synthetic-token", "synthetic-token", 409),
    (True, None, "synthetic-token", "synthetic-token", 200),
    (False, None, "synthetic-token", "synthetic-token", 200),
    (True, "synthetic_demo", "synthetic-token", "incorrect-token", 401),
    (False, "personal", None, "synthetic-token", 503),
])
def test_dataset_mode_and_existing_owner_auth_precede_business_operation(monkeypatch, demo, expected, configured, provided, status):
    settings = Settings(_env_file=None, llm_base_url="https://synthetic.example", llm_api_key="synthetic-model-key",
                        llm_model="synthetic-model", demo_mode=demo, owner_token=configured,
                        database_url="postgresql+psycopg://demo:synthetic@127.0.0.1:55432/" + ("polaris_demo" if demo else "polaris"))
    monkeypatch.setattr(personal, "get_settings", lambda: settings)
    effects, app = [], FastAPI()

    @app.post("/operation", dependencies=[Depends(personal.require_owner)])
    def operation():
        effects.append("executed")
        return {"ok": True}

    headers = {"Authorization": f"Bearer {provided}"}
    if expected is not None:
        headers["X-Polaris-Expected-Dataset"] = expected
    with TestClient(app) as client:
        response = client.post("/operation", headers=headers, json={"synthetic": True})
    assert response.status_code == status
    assert effects == (["executed"] if status == 200 else [])
    assert "synthetic-token" not in response.text and "synthetic-model-key" not in response.text
    if expected is not None and status == 200:
        assert settings.public_summary()["dataset"] == expected


def test_dataset_switch_after_health_check_still_rejects_business_request(monkeypatch):
    settings = Settings(_env_file=None, llm_base_url="https://synthetic.example", llm_api_key="synthetic-model-key",
                        llm_model="synthetic-model", demo_mode=False, owner_token="synthetic-token")
    monkeypatch.setattr(personal, "get_settings", lambda: settings)
    # 健康请求此前报告过synthetic_demo，实际执行时当前API是personal；不拿先前结果作当前证明。
    with pytest.raises(personal.HTTPException) as caught:
        personal.require_owner("Bearer synthetic-token", expected_dataset="synthetic_demo")
    assert caught.value.status_code == 409
