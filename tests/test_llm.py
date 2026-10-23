"""模型调用封装：记账、限流重试、结构化输出兜底、缓存、向量。全部用模拟的服务，不联网。"""
import json
from collections.abc import Callable
from typing import Any

import httpx2
import openai
import pytest
from pydantic import BaseModel

from polaris.config import Settings, get_settings
from polaris.llm import LLM, LLMError
from polaris.llm.recorder import MemoryRecorder

CHAT = "/v1/chat/completions"
EMBED = "/v1/embeddings"


class Item(BaseModel):
    name: str
    count: int


class FakeServer:
    """按路径依次返回预设的回答（最后一个一直重复），记下收到的每个请求。"""

    def __init__(self):
        self.routes: dict[str, list[tuple[int, Any]] | Callable] = {}
        self.calls: list[httpx2.Request] = []

    def on(self, path: str, *answers: tuple[int, Any] | Callable) -> "FakeServer":
        self.routes[path] = answers[0] if callable(answers[0]) else list(answers)
        return self

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.calls.append(request)
        route = self.routes[request.url.path]
        if callable(route):
            return route(request)
        status, body = route.pop(0) if len(route) > 1 else route[0]
        return httpx2.Response(status, json=body)

    def bodies(self, path: str) -> list[dict]:
        return [json.loads(c.content) for c in self.calls if c.url.path == path]

    def count(self, path: str) -> int:
        return sum(1 for c in self.calls if c.url.path == path)


def completion(content: str, prompt_tokens: int = 10, completion_tokens: int = 5) -> tuple[int, dict]:
    return 200, {"id": "c1", "object": "chat.completion", "created": 0, "model": "test-model",
                 "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                              "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                           "total_tokens": prompt_tokens + completion_tokens,
                           "completion_tokens_details": {"reasoning_tokens": 3}}}


def error(status: int, message: str) -> tuple[int, dict]:
    return status, {"error": {"message": message}}


def make_llm(server: FakeServer, **overrides) -> tuple[LLM, MemoryRecorder, list[float]]:
    rec, sleeps = MemoryRecorder(), []
    settings = Settings(**{**get_settings().model_dump(), **overrides}) if overrides else get_settings()
    client = httpx2.Client(transport=httpx2.MockTransport(server.handle))
    return LLM(settings, recorder=rec, sleep=sleeps.append, http_client=client), rec, sleeps


def test_optional_thinking_setting_reaches_request_and_record():
    server = FakeServer().on(CHAT, completion("收到"))
    llm, rec, _ = make_llm(server, llm_enable_thinking=False)
    llm.chat([{"role": "user", "content": "hi"}], purpose="thinking_config")
    assert server.bodies(CHAT)[0]["enable_thinking"] is False
    assert rec.rows[0]["request"]["extra_body"]["enable_thinking"] is False


def test_unconfigured_thinking_does_not_send_provider_specific_parameter():
    server = FakeServer().on(CHAT, completion("收到"))
    llm, _, _ = make_llm(server, llm_enable_thinking=None)
    llm.chat([{"role": "user", "content": "hi"}], purpose="thinking_default")
    assert "enable_thinking" not in server.bodies(CHAT)[0]


def test_explicit_call_settings_override_thinking_default_without_losing_other_fields():
    server = FakeServer().on(CHAT, completion("收到"))
    llm, _, _ = make_llm(server, llm_enable_thinking=False)
    llm.chat([{"role": "user", "content": "hi"}], purpose="thinking_override",
             extra_body={"enable_thinking": True, "custom_parameter": "kept"})
    assert server.bodies(CHAT)[0]["enable_thinking"] is True
    assert server.bodies(CHAT)[0]["custom_parameter"] == "kept"


def test_agent_model_preserves_provider_options_with_thinking_setting():
    from polaris.agent.models import chat_model

    settings = get_settings().model_copy(update={"llm_enable_thinking": False})
    model = chat_model(settings=settings, extra_body={"custom_parameter": "kept"})
    assert model.extra_body == {"enable_thinking": False, "custom_parameter": "kept"}


def test_chat_thinking_budget_does_not_change_structured_extraction():
    server = FakeServer().on(CHAT, completion("收到"), completion('{"name":"test","count":1}'))
    llm, rec, _ = make_llm(server, llm_enable_thinking=False,
                         llm_chat_enable_thinking=True, llm_chat_thinking_budget=1024)
    llm.chat([{"role": "user", "content": "hi"}], purpose="chat_budget",
             extra_body={"custom_parameter": "kept"})
    extracted = llm.extract("test", Item, purpose="extract_without_chat_budget", max_fix=0)
    assert extracted.ok
    chat_body, extract_body = server.bodies(CHAT)
    assert chat_body["enable_thinking"] is True and chat_body["thinking_budget"] == 1024
    assert chat_body["custom_parameter"] == "kept"
    assert extract_body["enable_thinking"] is False and "thinking_budget" not in extract_body
    assert rec.rows[0]["request"]["extra_body"]["thinking_budget"] == 1024


@pytest.mark.parametrize("role", ["chat", "extract", "judge", "brief"])
def test_agent_thinking_override_is_limited_to_chat_role(role):
    from polaris.agent.models import chat_model

    settings = get_settings().model_copy(update={"llm_enable_thinking": False,
        "llm_chat_enable_thinking": True, "llm_chat_thinking_budget": 1024})
    model = chat_model(role, settings=settings)
    assert model.extra_body["enable_thinking"] is (role == "chat")
    assert model.extra_body.get("thinking_budget") == (1024 if role == "chat" else None)


def test_explicit_thinking_disable_does_not_leak_default_chat_budget():
    from polaris.agent.models import chat_model

    settings = get_settings().model_copy(update={"llm_enable_thinking": False,
        "llm_chat_enable_thinking": True, "llm_chat_thinking_budget": 1024})
    model = chat_model(settings=settings, extra_body={"enable_thinking": False, "custom_parameter": "kept"})
    assert model.extra_body == {"enable_thinking": False, "custom_parameter": "kept"}


def test_chat_records_tokens_latency_and_no_key():
    server = FakeServer().on(CHAT, completion("收到"))
    llm, rec, _ = make_llm(server)
    assert llm.chat([{"role": "user", "content": "hi"}], purpose="t").content == "收到"
    row = rec.rows[0]
    assert (row["prompt_tokens"], row["completion_tokens"], row["reasoning_tokens"]) == (10, 5, 3)
    assert row["latency_ms"] >= 0 and row["provider"] == "llm.test" and row["model"] == "test-model"
    assert row["cost_usd"] is None  # 单价未知时不估
    assert "sk-test-chat" not in json.dumps(row, default=str)


def test_cost_computed_for_priced_model():
    server = FakeServer().on(CHAT, completion("ok", prompt_tokens=1_000_000, completion_tokens=1_000_000))
    llm, rec, _ = make_llm(server, llm_model="nvidia/nemotron-3-super-120b-a12b")
    llm.chat([{"role": "user", "content": "hi"}], purpose="t")
    assert float(rec.rows[0]["cost_usd"]) == pytest.approx(1.20)


def test_rate_limit_is_retried_and_every_attempt_recorded():
    server = FakeServer().on(CHAT, error(429, "Upstream rate limit exceeded"), completion("ok"))
    llm, rec, sleeps = make_llm(server)
    assert llm.chat([{"role": "user", "content": "hi"}], purpose="t").content == "ok"
    assert server.count(CHAT) == 2 and len(sleeps) == 1
    assert rec.rows[0]["error"].startswith("RateLimitError") and rec.rows[0]["attempt"] == 1
    assert rec.rows[1]["error"] is None and rec.rows[1]["attempt"] == 2


def test_retries_give_up_with_clear_error():
    server = FakeServer().on(CHAT, error(503, "down"))
    llm, rec, _ = make_llm(server)
    with pytest.raises(LLMError):
        llm.chat([{"role": "user", "content": "hi"}], purpose="t")
    assert len(rec.rows) == get_settings().llm_max_retries + 1


def test_client_errors_are_not_retried():
    server = FakeServer().on(CHAT, error(401, "bad key"))
    llm, rec, sleeps = make_llm(server)
    with pytest.raises(openai.AuthenticationError):
        llm.chat([{"role": "user", "content": "hi"}], purpose="t")
    assert server.count(CHAT) == 1 and not sleeps and rec.rows[0]["error"].startswith("AuthenticationError")


def test_extract_falls_back_to_json_object_when_json_schema_unsupported():
    server = FakeServer().on(CHAT, error(400, "This response_format type is unavailable now"),
                             completion('{"name": "a", "count": 2}'))
    llm, _, _ = make_llm(server)
    r = llm.extract("x", Item, purpose="t")
    assert r.ok and r.value == Item(name="a", count=2) and r.mode == "json_object" and r.attempts == 1
    assert [b["response_format"]["type"] for b in server.bodies(CHAT)] == ["json_schema", "json_object"]
    # 同一个模型第二次直接用 json_object，不再白试一次
    assert llm.extract("y", Item, purpose="t").ok
    assert server.bodies(CHAT)[-1]["response_format"]["type"] == "json_object"


def test_json_mode_setting_skips_json_schema():
    server = FakeServer().on(CHAT, completion('{"name": "a", "count": 1}'))
    llm, _, _ = make_llm(server, llm_json_mode="json_object")
    llm.extract("x", Item, purpose="t")
    assert server.bodies(CHAT)[0]["response_format"]["type"] == "json_object"


def test_extract_feeds_validation_error_back_and_retries():
    server = FakeServer().on(CHAT, completion('{"name": "a", "count": "很多"}'),
                             completion('```json\n{"name": "a", "count": 3}\n```'))
    llm, rec, _ = make_llm(server)
    r = llm.extract("x", Item, purpose="t")
    assert r.ok and r.value.count == 3 and r.attempts == 2
    assert rec.rows[0]["parsed_ok"] is False and rec.rows[0]["error"].startswith("validation:")
    assert "上一次的输出不合格" in server.bodies(CHAT)[1]["messages"][-1]["content"]


def test_extract_gives_up_after_max_fix():
    server = FakeServer().on(CHAT, completion("not json"))
    llm, _, _ = make_llm(server)
    r = llm.extract("x", Item, purpose="t", max_fix=1)
    assert not r.ok and r.attempts == 2 and "JSON" in r.error


def test_cache_hit_skips_network():
    server = FakeServer().on(CHAT, completion("ok"))
    llm, rec, _ = make_llm(server)
    msgs = [{"role": "user", "content": "same"}]
    first = llm.chat(msgs, purpose="t", cache=True)
    second = llm.chat(msgs, purpose="t", cache=True)
    assert server.count(CHAT) == 1
    assert not first.cache_hit and second.cache_hit and second.content == "ok"
    assert rec.rows[1]["cache_hit"] and rec.rows[1]["cost_usd"] == 0


def test_invalid_answers_are_not_served_from_cache():
    server = FakeServer().on(CHAT, completion("bad"), completion('{"name": "a", "count": 1}'))
    llm, _, _ = make_llm(server)
    assert not llm.extract("x", Item, purpose="t", cache=True, max_fix=0).ok
    assert llm.extract("x", Item, purpose="t", cache=True, max_fix=0).ok  # 不合格的那次没被当成缓存
    assert llm.extract("x", Item, purpose="t", cache=True, max_fix=0).ok  # 合格的这次被缓存了
    assert server.count(CHAT) == 2  # 第三次读的缓存，没走网络


def embeddings_answer(dim: int = 1024) -> Callable:
    def answer(request: httpx2.Request) -> httpx2.Response:
        n = len(json.loads(request.content)["input"])
        return httpx2.Response(200, json={
            "object": "list", "model": "test-embed",
            "data": [{"object": "embedding", "index": i, "embedding": [0.1] * dim} for i in range(n)],
            "usage": {"prompt_tokens": n, "total_tokens": n}})
    return answer


def test_embed_batches_and_checks_dimension():
    server = FakeServer().on(EMBED, embeddings_answer())
    llm, rec, _ = make_llm(server)
    vecs = llm.embed([f"t{i}" for i in range(23)], purpose="t")
    assert len(vecs) == 23 and len(vecs[0]) == 1024
    assert server.count(EMBED) == 3  # 每批 10 条
    assert server.calls[0].url.host == "embed.test"  # 走的是单独配置的向量服务
    assert server.bodies(EMBED)[0]["dimensions"] == 1024
    assert rec.rows[0]["response"] == {"count": 10, "dim": 1024, "usage": {"prompt_tokens": 10, "total_tokens": 10}}


def test_embed_wrong_dimension_raises():
    server = FakeServer().on(EMBED, embeddings_answer(dim=8))
    llm, _, _ = make_llm(server)
    with pytest.raises(LLMError):
        llm.embed(["a"], purpose="t")
