"""提词失败不能吞掉已经提交的主抽取结果和知识维护证据。"""
from contextlib import nullcontext
from types import SimpleNamespace

import polaris.daemon as daemon


def test_discovery_failure_still_reports_extraction_and_refreshes(monkeypatch):
    calls, logs = [], []
    result = {"new_ids": [5], "run_id": 7}
    collector = SimpleNamespace(close=lambda: calls.append("close"))
    monkeypatch.setattr(daemon.budget, "policy", lambda: {"xhs": {"round_queries": 1}})
    monkeypatch.setattr(daemon.budget, "hour_left", lambda _: 10)
    monkeypatch.setattr(daemon, "FACTORIES", {"xhs": lambda *args: collector})
    monkeypatch.setattr("polaris.collectors.locks.platform_lock", lambda *a, **k: nullcontext())
    monkeypatch.setattr(daemon, "collect_round", lambda *a, **k: result)
    monkeypatch.setattr(daemon, "get_settings", lambda: SimpleNamespace(llm_concurrency=1))
    monkeypatch.setattr("polaris.extract.run_extract", lambda *a, **k:
                        calls.append("extract") or {"extracted": 1, "failed": 2})

    def discover(*args):
        calls.append("discover")
        raise RuntimeError("synthetic discovery failure")

    monkeypatch.setattr("polaris.agent.scouts.discover_queries", discover)
    monkeypatch.setattr("polaris.search.maintenance.request_refresh", lambda *args: calls.append("request"))
    monkeypatch.setattr(daemon, "maintain_knowledge", lambda *args: calls.append("maintain"))
    monkeypatch.setattr(daemon, "proactive", lambda *args: calls.append("proactive"))

    assert daemon.run_round("xhs", None, 10, 0, echo=logs.append) is result
    assert calls == ["close", "extract", "discover", "request", "maintain", "proactive"]
    assert "  [xhs] 抽取 1 条，失败 2 条" in logs
    assert "synthetic discovery failure" in logs[-1]
