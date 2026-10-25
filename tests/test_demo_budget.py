"""#17: the public demo stops calling the model once today's budget or a visitor's share is used up."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from polaris import demo_budget
from polaris.api.personal import router
from polaris.db.models import LLMCall
from polaris.db.session import session_scope

NOW = datetime(2026, 10, 25, 14, tzinfo=UTC)  # 22:00 Beijing


def settings(**kw):
    return SimpleNamespace(**{"demo_mode": True, "demo_daily_model_calls": 100, "demo_requests_per_client": 100, **kw})


def visitor(ip="203.0.113.7"):
    return SimpleNamespace(headers={"x-forwarded-for": f"{ip}, 10.0.0.2"}, client=SimpleNamespace(host="127.0.0.1"))


@pytest.fixture(autouse=True)
def _fresh_window():
    demo_budget._recent.clear()
    yield
    demo_budget._recent.clear()


def call(at):
    return LLMCall(purpose="chat", provider="test", model="test-model", endpoint="chat", request={}, latency_ms=1, created_at=at)


def test_guard_does_nothing_outside_demo_mode(monkeypatch):
    monkeypatch.setattr(demo_budget, "get_settings", lambda: settings(demo_mode=False, demo_requests_per_client=0))
    monkeypatch.setattr(demo_budget, "calls_today", lambda now: pytest.fail("personal mode must not count calls"))
    demo_budget.guard(visitor())


def test_daily_cap_counts_model_calls_since_beijing_midnight(db, monkeypatch):
    midnight = demo_budget.beijing_midnight(NOW)
    with session_scope() as s:
        s.add_all([call(midnight - timedelta(minutes=1)), call(midnight), call(NOW - timedelta(minutes=5))])
    assert demo_budget.calls_today(NOW) == 2

    monkeypatch.setattr(demo_budget, "_now", lambda: NOW)
    monkeypatch.setattr(demo_budget, "get_settings", lambda: settings(demo_daily_model_calls=2))
    with pytest.raises(HTTPException) as refused:
        demo_budget.guard(visitor())
    assert refused.value.status_code == 429 and refused.value.detail == demo_budget.DAILY_MESSAGE

    monkeypatch.setattr(demo_budget, "get_settings", lambda: settings(demo_daily_model_calls=3))
    demo_budget.guard(visitor())


def test_each_visitor_gets_a_share_per_ten_minutes(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(demo_budget, "get_settings", lambda: settings(demo_requests_per_client=2))
    monkeypatch.setattr(demo_budget, "calls_today", lambda now: 0)
    monkeypatch.setattr(demo_budget.time, "monotonic", lambda: clock[0])
    demo_budget.guard(visitor())
    demo_budget.guard(visitor())
    with pytest.raises(HTTPException) as refused:
        demo_budget.guard(visitor())
    assert refused.value.status_code == 429 and refused.value.detail == demo_budget.CLIENT_MESSAGE
    demo_budget.guard(visitor("198.51.100.4"))  # another visitor is unaffected
    clock[0] += demo_budget.WINDOW_SECONDS + 1
    demo_budget.guard(visitor())


@pytest.mark.parametrize("path", ["/api/me/chat/tasks", "/api/me/chat/{thread_id}/resume/tasks",
                                  "/api/me/jobs/{reference}/followup/draft", "/api/me/watches/{watch_id}/report"])
def test_every_route_that_calls_a_model_is_guarded(path):
    route = next(r for r in router.routes if r.path == path and "POST" in r.methods)
    assert demo_budget.guard in [d.dependency for d in route.dependencies]
