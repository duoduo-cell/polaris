"""把 LangChain 里的模型调用写进 llm_calls，和 M1 封装的调用记在同一张表里，评测页统一读。

按 OpenTelemetry 生成式 AI 规范的思路：一次模型调用一条记录，带模型、token、耗时、出错信息；
工具调用由 tools/runtime.py 写进 tool_calls。
"""
import time
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage, convert_to_openai_messages
from langchain_core.outputs import LLMResult

from polaris.config import get_settings
from polaris.llm.pricing import cost_usd
from polaris.llm.recorder import DbRecorder, Recorder
from polaris.replay import Cassette

SECRET_HINTS = ("key", "secret", "token", "password")


def message_to_dict(msg: BaseMessage) -> dict[str, Any]:
    data: dict[str, Any] = {"role": "assistant", "content": msg.content}
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        data["tool_calls"] = [{"id": c.get("id"), "name": c["name"], "args": c["args"]} for c in tool_calls]
    return data


class PolarisTracer(BaseCallbackHandler):
    def __init__(self, *, purpose: str, run_id: int | None = None, prompt_version: str | None = None,
                 recorder: Recorder | None = None, cassette: Cassette | None = None, provider: str | None = None):
        self.purpose, self.run_id, self.prompt_version = purpose, run_id, prompt_version
        self.recorder = recorder or DbRecorder()
        self.cassette = cassette
        self.provider = provider or urlparse(get_settings().chat_base).netloc
        self._starts: dict[UUID, dict[str, Any]] = {}
        self.call_ids: list[int | None] = []

    def on_chat_model_start(self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *,
                            run_id: UUID, invocation_params: dict[str, Any] | None = None,
                            **kwargs: Any) -> None:
        params = {k: v for k, v in (invocation_params or {}).items()
                  if not any(h in k.lower() for h in SECRET_HINTS) and k not in ("tools",)}
        tools = [t.get("function", {}).get("name") for t in (invocation_params or {}).get("tools", []) or []]
        self._starts[run_id] = {"t": time.perf_counter(), "params": params, "tools": tools,
                                "messages": convert_to_openai_messages(messages[0]) if messages else []}

    def _model(self, start: dict[str, Any]) -> str:
        p = start["params"]
        return str(p.get("model") or p.get("model_name") or "unknown")

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        start = self._starts.pop(run_id, None) or {"t": time.perf_counter(), "params": {}, "tools": [], "messages": []}
        gen = response.generations[0][0] if response.generations and response.generations[0] else None
        msg = getattr(gen, "message", None)
        usage = (getattr(msg, "usage_metadata", None) or {}) if msg is not None else {}
        out_details = usage.get("output_token_details") or {}
        in_details = usage.get("input_token_details") or {}
        model = self._model(start)
        message = message_to_dict(msg) if msg is not None else {"content": getattr(gen, "text", "")}
        if self.cassette is not None:
            self.cassette.record_llm(message)
        self.call_ids.append(self.recorder.record({
            "run_id": self.run_id, "purpose": self.purpose, "prompt_version": self.prompt_version,
            "provider": self.provider, "model": model, "endpoint": "chat",
            "request": {"messages": start["messages"], "params": start["params"], "tools": start["tools"]},
            "response": {"message": message, "usage": usage}, "parsed_ok": None, "error": None, "attempt": 1,
            "latency_ms": int((time.perf_counter() - start["t"]) * 1000),
            "prompt_tokens": usage.get("input_tokens"), "completion_tokens": usage.get("output_tokens"),
            "reasoning_tokens": out_details.get("reasoning"), "cached_tokens": in_details.get("cache_read"),
            "cost_usd": cost_usd(model, usage.get("input_tokens"), usage.get("output_tokens")),
        }))

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        start = self._starts.pop(run_id, None) or {"t": time.perf_counter(), "params": {}, "tools": [], "messages": []}
        self.call_ids.append(self.recorder.record({
            "run_id": self.run_id, "purpose": self.purpose, "prompt_version": self.prompt_version,
            "provider": self.provider, "model": self._model(start), "endpoint": "chat",
            "request": {"messages": start["messages"], "params": start["params"], "tools": start["tools"]},
            "response": None, "error": f"{type(error).__name__}: {error}", "attempt": 1,
            "latency_ms": int((time.perf_counter() - start["t"]) * 1000)}))
