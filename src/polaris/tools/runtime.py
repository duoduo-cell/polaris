"""工具的统一执行入口。LangChain agent、MCP 服务、评测都从这里调工具，所以下面几件事只在这里做一次：

- 参数校验：不合格时把"哪里错了、怎么改、正确示例"返回给模型，而不是抛异常
- 故障注入：评测时按配置让某次调用返回限流或超时，看 agent 能不能恢复
- 录制回放：见 polaris/replay.py
- 记账：每次调用（包括失败的、回放的）写一条 tool_calls
"""
import json
import time
from collections import defaultdict, deque
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import ValidationError
from sqlalchemy import Engine

from polaris.db.models import ToolCall
from polaris.db.session import session_scope
from polaris.replay import Cassette, CassetteMiss
from polaris.tools.base import ToolError, ToolSpec, render, validation_error

FaultKind = Literal["rate_limit", "timeout"]
FAULT_MESSAGES: dict[str, str] = {
    "rate_limit": "出错了：来源暂时限流（HTTP 429）。\n怎么改：稍等一下，用同样的参数再调用一次。",
    "timeout": "出错了：来源超时没有响应。\n怎么改：用同样的参数再调用一次；连续失败就换个查询词。",
}
INTERNAL_ERROR = "出错了：工具内部错误（{name}）。\n怎么改：这不是参数的问题，可以再试一次，或者换一个工具。"


class ToolRuntime:
    def __init__(self, *, run_id: int | None = None, thread_id: str | None = None, agent: str | None = None,
                 cassette: Cassette | None = None, faults: dict[str, list[FaultKind]] | None = None,
                 record_db: bool = True, engine: Engine | None = None):
        self.run_id, self.thread_id, self.agent = run_id, thread_id, agent
        self.cassette = cassette
        self.faults: dict[str, deque[FaultKind]] = defaultdict(deque, {k: deque(v) for k, v in (faults or {}).items()})
        self.record_db = record_db
        self.engine = engine
        self.calls: list[dict[str, Any]] = []  # 本次运行的调用，评测时直接读
        self.results: dict[int, Any] = {}  # 完整结构化结果给界面；不受模型文本长度限制影响

    def invoke(self, spec: ToolSpec, raw_args: dict[str, Any]) -> str:
        start = time.perf_counter()
        try:
            args = spec.args_schema.model_validate(raw_args)
        except ValidationError as e:
            return self._done(spec, raw_args, validation_error(spec, e).render(), False, "invalid_args", start)
        normalized = args.model_dump(mode="json")

        if self.faults[spec.name]:
            fault = self.faults[spec.name].popleft()
            return self._done(spec, normalized, FAULT_MESSAGES[fault], False, "injected", start)

        if self.cassette is not None and self.cassette.mode == "replay":
            try:
                entry = self.cassette.replay_tool(spec.name, normalized)
            except CassetteMiss as e:
                return self._done(spec, normalized, f"出错了：{e}", False, "replay_miss", start, replayed=True)
            try:
                data = json.loads(entry["output"]) if entry["ok"] else None
            except ValueError:
                data = None
            return self._done(spec, normalized, entry["output"], entry["ok"], entry.get("error_kind"), start,
                              replayed=True, data=data)

        try:
            data = spec.func(args)
            output, ok, kind = render(data), True, None
        except ToolError as e:
            data = None
            output, ok, kind = e.render(), False, "tool_error"
        except Exception as e:  # noqa: BLE001  工具自己的 bug 不能让 agent 崩掉
            data = None
            output, ok, kind = INTERNAL_ERROR.format(name=type(e).__name__), False, "internal"
        if self.cassette is not None:
            self.cassette.record_tool(spec.name, normalized, output, ok, kind)
        return self._done(spec, normalized, output, ok, kind, start, data=data)

    def _done(self, spec: ToolSpec, args: dict[str, Any], output: str, ok: bool, kind: str | None,
              start: float, replayed: bool = False, data: Any = None) -> str:
        row = {"run_id": self.run_id, "thread_id": self.thread_id, "agent": self.agent, "tool": spec.name,
               "args": args, "output": output, "ok": ok, "error_kind": kind,
               "latency_ms": int((time.perf_counter() - start) * 1000), "replayed": replayed}
        self.calls.append(row)
        if data is not None:
            self.results[len(self.calls) - 1] = data
        if self.record_db:
            with session_scope(self.engine) as s:
                s.add(ToolCall(**row))
        return output

    def as_langchain_tool(self, spec: ToolSpec) -> StructuredTool:
        """给 LangChain agent 用。参数 schema 以 JSON Schema 形式交给模型，校验留在 invoke 里做，
        这样参数不合格的调用也会被记账，模型也能拿到"怎么改"的提示。"""
        def run(**kwargs: Any) -> str:
            return self.invoke(spec, kwargs)

        schema = spec.args_schema.model_json_schema()
        return StructuredTool.from_function(func=run, name=spec.name, description=spec.description,
                                            args_schema=schema, infer_schema=False)

    def langchain_tools(self, specs: list[ToolSpec]) -> list[StructuredTool]:
        return [self.as_langchain_tool(s) for s in specs]
