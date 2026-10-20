"""Transient public GET failures may recover; server refusals must never be retried."""
import httpx
import pytest

from polaris.collectors import tencent


@pytest.mark.parametrize("failure", [httpx.ConnectTimeout, httpx.ReadTimeout])
def test_transient_network_failure_recovers_without_changing_query(monkeypatch, failure):
    requests = []
    sleeps = []
    monkeypatch.setattr(tencent.time, "sleep", sleeps.append)

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            raise failure("temporary network failure", request=request)
        return httpx.Response(200, json={"Code": 200, "Data": {"PostId": "42"}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        collector = tencent.TencentCollector(client=client, pause=(0, 0))
        assert collector.detail("42") == {"PostId": "42"}
    assert len(requests) == 2
    assert all(request.url.params["postId"] == "42" for request in requests)
    assert sleeps == [2.0, 0.0]


def test_network_retries_are_bounded(monkeypatch):
    requests = []
    sleeps = []
    monkeypatch.setattr(tencent.time, "sleep", sleeps.append)

    def handle(request):
        requests.append(request)
        raise httpx.ReadTimeout("still unavailable", request=request)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.ReadTimeout):
            tencent.TencentCollector(client=client).detail("42")
    assert len(requests) == 3
    assert sleeps == [2.0, 4.0]


@pytest.mark.parametrize("status", [403, 429])
def test_http_refusal_is_not_retried(monkeypatch, status):
    requests = []
    monkeypatch.setattr(tencent.time, "sleep", lambda _: pytest.fail("must not retry a server refusal"))

    def handle(request):
        requests.append(request)
        return httpx.Response(status)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            tencent.TencentCollector(client=client).detail("42")
    assert len(requests) == 1


def test_business_error_is_not_retried(monkeypatch):
    requests = []
    monkeypatch.setattr(tencent.time, "sleep", lambda _: pytest.fail("must not retry a business error"))

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"Code": 429, "Data": {}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(RuntimeError, match="Code=429"):
            tencent.TencentCollector(client=client).detail("42")
    assert len(requests) == 1
