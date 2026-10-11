"""统一的模型调用封装。所有模型调用都走这里，每次调用（包括失败的重试和读缓存的）都记一条 llm_calls。

- chat：对话和工具调用
- extract：结构化抽取。先试 json_schema；服务不支持就改用 json_object，本地按 schema 校验，不合格带着错误重试
- embed：向量，按批调用，核对维度
- rerank：重排（M4 检索用），返回每个候选的相关度
"""
import hashlib
import json
import random
import re
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar
from urllib.parse import urlparse

import openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from polaris.config import Role, Settings, get_settings
from polaris.llm.pricing import cost_usd
from polaris.llm.recorder import DbRecorder, Recorder

T = TypeVar("T", bound=BaseModel)

RETRYABLE = (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError,
             openai.InternalServerError)

JSON_INSTRUCTION = (
    "只输出一个 JSON 对象，必须符合下面的 JSON Schema。不要输出解释，不要加代码块标记。"
    "原文里没有的信息填 null，不要猜。\nJSON Schema：\n{schema}"
)
FIX_INSTRUCTION = "上一次的输出不合格：{error}\n请只输出修正后的 JSON 对象。"


class LLMError(RuntimeError):
    pass


@dataclass
class ChatResult:
    content: str | None
    message: dict[str, Any]
    usage: dict[str, Any] | None
    call_id: int | None
    cache_hit: bool = False

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return self.message.get("tool_calls") or []

    def as_message(self) -> dict[str, Any]:
        """放回对话历史用：只留标准字段，去掉思考过程之类的扩展字段。"""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        return msg


@dataclass
class ExtractResult(Generic[T]):
    value: T | None
    ok: bool
    mode: str
    attempts: int
    call_ids: list[int | None] = field(default_factory=list)
    error: str | None = None


def cache_key(endpoint: str, body: dict[str, Any]) -> str:
    raw = json.dumps({"endpoint": endpoint, **body}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def usage_fields(usage: dict[str, Any] | None) -> dict[str, int | None]:
    u = usage or {}
    return {
        "prompt_tokens": u.get("prompt_tokens"),
        "completion_tokens": u.get("completion_tokens"),
        "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        "cached_tokens": (u.get("prompt_tokens_details") or {}).get("cached_tokens"),
    }


def strip_fences(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    return m.group(1) if m else text


def parse_into(data: dict[str, Any], schema: type[T]) -> tuple[bool, str | None, T | None]:
    try:
        content = data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        return False, "回答里没有 choices[0].message", None
    try:
        return True, None, schema.model_validate(json.loads(strip_fences(content)))
    except json.JSONDecodeError as e:
        return False, f"不是合法的 JSON：{e.msg}（第 {e.pos} 个字符）", None
    except ValidationError as e:
        problems = "；".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
        return False, f"不符合 schema：{problems}", None


def is_response_format_unsupported(err: openai.BadRequestError) -> bool:
    text = str(err).lower()
    return "response_format" in text or "json_schema" in text


class LLM:
    def __init__(self, settings: Settings | None = None, recorder: Recorder | None = None,
                 sleep: Callable[[float], None] = time.sleep, http_client: Any = None):
        """http_client：测试时传一个带模拟传输层的客户端（OpenAI SDK 3.x 用的是 httpx2）。"""
        s = settings or get_settings()
        self.settings = s
        self.recorder = recorder or DbRecorder()
        self._sleep = sleep
        # 重试自己做，每次失败都要记下来，所以关掉 SDK 自带的重试
        self.chat_client = OpenAI(base_url=s.chat_base, api_key=s.llm_api_key.get_secret_value(),
                                  timeout=s.llm_timeout, max_retries=0, http_client=http_client)
        self.embed_client = OpenAI(base_url=s.embed_base, api_key=s.embed_key.get_secret_value(),
                                   timeout=s.llm_timeout, max_retries=0, http_client=http_client)
        self.rerank_client = (OpenAI(base_url=s.rerank_base_url, api_key=s.embed_key.get_secret_value(),
                                     timeout=s.llm_timeout, max_retries=0, http_client=http_client)
                              if s.rerank_enabled else None)
        self._no_json_schema: set[str] = set()  # 已知不支持 json_schema 的模型，下次直接用 json_object
        self._lock = threading.Lock()

    # ---- 对外的三个函数 ----

    def chat(self, messages: list[dict[str, Any]], *, purpose: str, role: Role = "chat",
             tools: list[dict[str, Any]] | None = None, tool_choice: Any = None, run_id: int | None = None,
             prompt_version: str | None = None, cache: bool | None = None, **params: Any) -> ChatResult:
        body: dict[str, Any] = {"model": self.settings.model_for(role), "messages": messages, **params}
        if tools:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        data, call_id, hit, _ = self._request("chat", body, purpose=purpose, run_id=run_id,
                                              prompt_version=prompt_version, cache=cache)
        msg = data["choices"][0]["message"]
        return ChatResult(content=msg.get("content"), message=msg, usage=data.get("usage"),
                          call_id=call_id, cache_hit=hit)

    def extract(self, prompt: str | list[dict[str, Any]], schema: type[T], *, purpose: str,
                role: Role = "extract", system: str | None = None, run_id: int | None = None,
                prompt_version: str | None = None, cache: bool | None = None, max_fix: int = 2,
                **params: Any) -> ExtractResult[T]:
        model = self.settings.model_for(role)
        schema_json = schema.model_json_schema()
        instruction = JSON_INSTRUCTION.format(schema=json.dumps(schema_json, ensure_ascii=False))
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": f"{system}\n\n{instruction}" if system else instruction}]
        messages += [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt

        result: ExtractResult[T] = ExtractResult(value=None, ok=False, mode="", attempts=0)
        fixes = 0
        while True:
            known_no = self.settings.llm_json_mode == "json_object" or model in self._no_json_schema
            mode = "json_object" if known_no else "json_schema"
            body = {"model": model, "messages": messages, "response_format": self._response_format(
                mode, schema.__name__, schema_json), **params}
            try:
                data, call_id, _, (ok, error, value) = self._request(
                    "chat", body, purpose=purpose, run_id=run_id, prompt_version=prompt_version, cache=cache,
                    check=lambda d: parse_into(d, schema))
            except openai.BadRequestError as e:
                if mode == "json_schema" and is_response_format_unsupported(e):
                    with self._lock:
                        self._no_json_schema.add(model)
                    continue  # 换 json_object 再来，不算一次修正
                raise
            result.attempts += 1
            result.call_ids.append(call_id)
            result.mode = mode
            if ok:
                result.value, result.ok, result.error = value, True, None
                return result
            result.error = error
            if fixes >= max_fix:
                return result
            fixes += 1
            content = data["choices"][0]["message"].get("content") or ""
            messages = messages + [{"role": "assistant", "content": content},
                                   {"role": "user", "content": FIX_INSTRUCTION.format(error=error)}]

    def embed(self, texts: list[str], *, purpose: str, run_id: int | None = None) -> list[list[float]]:
        s = self.settings
        vectors: list[list[float]] = []
        for i in range(0, len(texts), s.embed_batch):
            body = {"model": s.embed_model, "input": texts[i:i + s.embed_batch], "dimensions": s.embed_dim}
            data, _, _, _ = self._request("embeddings", body, purpose=purpose, run_id=run_id, cache=False)
            batch = [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
            bad = {len(v) for v in batch} - {s.embed_dim}
            if bad:
                raise LLMError(f"向量维度是 {sorted(bad)}，配置要求 {s.embed_dim}")
            vectors.extend(batch)
        return vectors

    def rerank(self, query: str, documents: list[str], *, purpose: str, top_n: int | None = None,
               run_id: int | None = None) -> list[tuple[int, float]]:
        """返回 (候选的下标, 相关度)，相关度高的在前。相关度只在同一次请求内可比（百炼文档的说明）。"""
        if self.rerank_client is None:
            raise LLMError("没有配置重排服务（RERANK_BASE_URL、RERANK_MODEL）")
        body: dict[str, Any] = {"model": self.settings.rerank_model, "query": query, "documents": documents}
        if top_n:
            body["top_n"] = top_n
        data, _, _, _ = self._request("rerank", body, purpose=purpose, run_id=run_id, cache=False)
        return [(r["index"], float(r["relevance_score"])) for r in data.get("results") or []]

    # ---- 内部 ----

    @staticmethod
    def _response_format(mode: str, name: str, schema_json: dict[str, Any]) -> dict[str, Any]:
        if mode == "json_object":
            return {"type": "json_object"}
        return {"type": "json_schema", "json_schema": {"name": name, "schema": schema_json, "strict": False}}

    def _backoff(self, attempt: int, err: Exception) -> float:
        response = getattr(err, "response", None)
        retry_after = response.headers.get("retry-after") if response is not None else None
        try:
            if retry_after:
                return min(60.0, float(retry_after))
        except ValueError:
            pass
        return min(60.0, 2.0 ** attempt + random.uniform(0, 1))

    def _request(self, endpoint: str, body: dict[str, Any], *, purpose: str, run_id: int | None = None,
                 prompt_version: str | None = None, cache: bool | None = None,
                 check: Callable[[dict[str, Any]], tuple[bool, str | None, Any]] | None = None):
        """发一次请求：读缓存、限流时等待重试、记账。返回 (回答, 记录编号, 是否读了缓存, 校验结果)。"""
        is_embed = endpoint == "embeddings"
        if endpoint == "rerank":
            client, base = self.rerank_client, self.settings.rerank_base_url or ""
        else:
            client = self.embed_client if is_embed else self.chat_client
            base = self.settings.embed_base if is_embed else self.settings.chat_base
        use_cache = (self.settings.llm_cache if cache is None else cache) and not is_embed
        key = cache_key(endpoint, body)
        common = {"run_id": run_id, "purpose": purpose, "prompt_version": prompt_version,
                  "provider": urlparse(base).netloc, "model": body["model"], "endpoint": endpoint,
                  "request": body, "cache_key": key}
        if endpoint == "rerank":  # 候选原文不进记录（可能是帖子原文），只记条数
            common["request"] = {k: v for k, v in body.items() if k != "documents"} | {"documents": len(body["documents"])}

        if use_cache:
            cached = self.recorder.find_cached(key)
            if cached is not None:
                checked = check(cached) if check else (None, None, None)
                call_id = self.recorder.record({
                    **common, "response": cached, "parsed_ok": checked[0], "error": None,
                    "latency_ms": 0, "cache_hit": True, "prompt_tokens": 0, "completion_tokens": 0,
                    "cost_usd": 0})
                return cached, call_id, True, checked

        for attempt in range(1, self.settings.llm_max_retries + 2):
            start = time.perf_counter()
            try:
                if is_embed:
                    resp = client.embeddings.create(**body)
                elif endpoint == "rerank":  # OpenAI SDK 没有重排接口，用它的通用 POST（百炼的 /reranks）
                    resp = client.post("/reranks", body=body, cast_to=object)
                else:
                    resp = client.chat.completions.create(**body)
            except RETRYABLE as e:
                self.recorder.record({**common, "error": f"{type(e).__name__}: {e}", "attempt": attempt,
                                      "latency_ms": int((time.perf_counter() - start) * 1000)})
                if attempt > self.settings.llm_max_retries:
                    raise LLMError(f"{purpose}：重试 {attempt - 1} 次后仍失败：{e}") from e
                self._sleep(self._backoff(attempt, e))
                continue
            except openai.APIStatusError as e:  # 400、401、404 之类，重试也没用
                self.recorder.record({**common, "error": f"{type(e).__name__}: {e}", "attempt": attempt,
                                      "latency_ms": int((time.perf_counter() - start) * 1000)})
                raise
            latency = int((time.perf_counter() - start) * 1000)
            data = resp if isinstance(resp, dict) else resp.model_dump(mode="json")
            usage = usage_fields(data.get("usage"))
            if endpoint == "rerank":  # 重排只返回 total_tokens
                usage["prompt_tokens"] = (data.get("usage") or {}).get("total_tokens")
            checked = check(data) if check else (None, None, None)
            if is_embed:  # 向量本身不进记录，只记条数和维度
                vecs = data.get("data") or []
                logged = {"count": len(vecs), "dim": len(vecs[0]["embedding"]) if vecs else None,
                          "usage": data.get("usage")}
            else:
                logged = data
            call_id = self.recorder.record({
                **common, "response": logged, "parsed_ok": checked[0],
                "error": f"validation: {checked[1]}" if checked[1] else None, "attempt": attempt,
                "latency_ms": latency, **usage,
                "cost_usd": cost_usd(body["model"], usage["prompt_tokens"], usage["completion_tokens"])})
            return data, call_id, False, checked
        raise LLMError("unreachable")


def parallel_map(fn: Callable[[Any], Any], items: Iterable[Any], concurrency: int) -> list[Any]:
    """并发跑 fn，结果按输入顺序返回；单个失败返回异常对象，不影响其他的。"""
    def safe(item: Any) -> Any:
        try:
            return fn(item)
        except Exception as e:  # noqa: BLE001
            return e

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        return list(pool.map(safe, items))
