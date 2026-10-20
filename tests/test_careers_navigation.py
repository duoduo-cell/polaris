"""Navigation may recover a timeout, but must stop on an explicit server refusal."""
from types import SimpleNamespace

import pytest
from playwright.sync_api import TimeoutError as NavigationTimeout

from polaris.collectors import sites


def test_timeout_recovers_and_keeps_destination(monkeypatch):
    calls = []
    sleeps = []
    monkeypatch.setattr(sites.time, "sleep", sleeps.append)

    def goto(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise NavigationTimeout("temporary navigation failure")
        return SimpleNamespace(status=200)

    sites.XhsCareersCollector._navigate(SimpleNamespace(goto=goto), "https://careers.test/job/42")
    assert calls == ["https://careers.test/job/42"] * 2
    assert sleeps == [2.0]


def test_persistent_navigation_timeout_is_bounded(monkeypatch):
    calls = []
    sleeps = []
    monkeypatch.setattr(sites.time, "sleep", sleeps.append)

    def goto(url, **kwargs):
        calls.append(url)
        raise NavigationTimeout("still unavailable")

    with pytest.raises(NavigationTimeout):
        sites.XhsCareersCollector._navigate(SimpleNamespace(goto=goto), "https://careers.test/job/42")
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]


@pytest.mark.parametrize("status", [403, 429])
def test_navigation_does_not_retry_server_refusal(monkeypatch, status):
    calls = []
    monkeypatch.setattr(sites.time, "sleep", lambda _: pytest.fail("must not retry an explicit refusal"))

    def goto(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status=status)

    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        sites.XhsCareersCollector._navigate(SimpleNamespace(goto=goto), "https://careers.test/job/42")
    assert len(calls) == 1
