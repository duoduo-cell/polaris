import json
from types import SimpleNamespace

import pytest

from polaris.collectors import boss


def test_browser_login_replaces_stale_cli_cache_atomically(tmp_path, monkeypatch):
    path = tmp_path / "credential.json"
    path.write_text(json.dumps({"cookies": {"wt2": "old"}, "saved_at": 1}))
    fresh = {k: "new-" + k for k in ("wt2", "zp_at", "wbg", "__zp_stoken__")}
    monkeypatch.setattr(boss, "_browser_cookies", lambda: fresh)
    assert boss.sync_browser_credential(path)
    saved = json.loads(path.read_text())
    assert saved["cookies"] == fresh and saved["saved_at"] > 1
    before = path.read_bytes()
    assert not boss.sync_browser_credential(path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_missing_browser_login_does_not_erase_saved_credentials(tmp_path, monkeypatch):
    path = tmp_path / "credential.json"
    path.write_text('{"cookies":{"wt2":"old"}}')
    before = path.read_bytes()
    monkeypatch.setattr(boss, "_browser_cookies", lambda: {"wt2": "incomplete"})
    assert not boss.sync_browser_credential(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("status,expected", [
    ({"search_authenticated": False, "authenticated": True, "reason": "搜索令牌过期"}, False),
    ({"search_authenticated": True, "authenticated": True}, True),
    ({"authenticated": True}, True),
    ({"data": {"search_authenticated": True}}, True),
    ({"data": []}, False),
])
def test_auth_preserves_search_failure_and_syncs_before_check(monkeypatch, status, expected):
    calls = []
    monkeypatch.setattr(boss, "sync_browser_credential", lambda: calls.append("sync") or True)

    def run(*args, **kwargs):
        calls.append("status")
        return SimpleNamespace(stdout=json.dumps(status), returncode=0)

    monkeypatch.setattr(boss.subprocess, "run", run)
    result = boss.auth_status()
    assert result["authenticated"] is expected
    assert calls == ["sync", "status"]
    if status.get("reason"):
        assert result["reason"] == status["reason"]


def test_sync_failure_is_redacted_and_not_misreported_as_logged_out(monkeypatch):
    def fail():
        raise RuntimeError("secret-cookie-value")

    monkeypatch.setattr(boss, "sync_browser_credential", fail)
    monkeypatch.setattr(boss.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout='{"search_authenticated": false}'))
    result = boss.auth_status()
    assert "RuntimeError" in result["reason"]
    assert "secret-cookie-value" not in str(result)


@pytest.mark.parametrize("ok", [True, False])
def test_daemon_uses_search_health_and_reports_reason(monkeypatch, ok):
    from contextlib import nullcontext

    from polaris import daemon

    logs, rounds = [], []
    monkeypatch.setattr(daemon, "IMPLEMENTED", ("boss",))
    monkeypatch.setattr(daemon, "maintain_knowledge", lambda _: None)
    monkeypatch.setattr(daemon.budget, "in_window", lambda _: True)
    monkeypatch.setattr(daemon.budget, "today_cap", lambda _: (200, "test"))
    monkeypatch.setattr(daemon.budget, "used_today", lambda _: 0)
    monkeypatch.setattr(daemon.budget, "hour_left", lambda _: 200)
    monkeypatch.setattr(daemon.budget, "policy", lambda: {"boss": {"via": "cli"}})
    monkeypatch.setattr(daemon, "is_running", lambda: True)
    monkeypatch.setattr(daemon, "attached", lambda: nullcontext(None))
    monkeypatch.setattr(daemon, "open_browser", lambda *a, **k: None)
    monkeypatch.setattr(daemon, "notify", lambda *a: None)
    monkeypatch.setattr(boss, "auth_status", lambda: {"authenticated": ok, "reason": "CLI 超时"})
    monkeypatch.setattr(daemon, "run_round", lambda *a: rounds.append(a) or {})
    assert daemon.tick(echo=logs.append) is ok
    assert bool(rounds) is ok
    if not ok:
        assert any("CLI 超时" in log for log in logs)
        assert not any("没登录" in log for log in logs)
