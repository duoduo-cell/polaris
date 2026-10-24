"""工具的统一执行入口。LangChain agent、MCP 服务、评测都从这里调工具，所以下面几件事只在这里做一次：

- 参数校验：不合格时把"哪里错了、怎么改、正确示例"返回给模型，而不是抛异常
- 故障注入：评测时按配置让某次调用返回限流或超时，看 agent 能不能恢复
- 录制回放：见 polaris/replay.py
- 记账：每次调用（包括失败的、回放的）写一条 tool_calls
"""
import json
import re
import time
from collections import defaultdict, deque
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import ValidationError
from sqlalchemy import Engine

from polaris.data_scope import demo_tool_allowed
from polaris.db.models import ToolCall
from polaris.db.session import session_scope
from polaris.replay import Cassette, CassetteMiss
from polaris.tools.base import FinalResponse, ToolError, ToolSpec, render, validation_error

FaultKind = Literal["rate_limit", "timeout"]
FAULT_MESSAGES: dict[str, str] = {
    "rate_limit": "出错了：来源暂时限流（HTTP 429）。\n怎么改：稍等一下，用同样的参数再调用一次。",
    "timeout": "出错了：来源超时没有响应。\n怎么改：用同样的参数再调用一次；连续失败就换个查询词。",
}
INTERNAL_ERROR = "出错了：工具内部错误（{name}）。\n怎么改：这不是参数的问题，可以再试一次，或者换一个工具。"
# 和用户对话时一次回答里每种工具最多调几次（10-12 探针：提示词说了"最多换一次说法"，模型还是把帖子搜了 41 次；
# 回答评测 E2：查一个不存在的"每周 2 天"岗位，岗位和帖子各搜满 8 次，于是搜索再收到 6 次）
PER_ANSWER_CAPS = {"jobs_search": 6, "posts_search": 6, "questions_search": 5, "kg_stats": 8,
                   "jobs_get": 10, "posts_get": 10, "career_report": 1}
# 软刹车：这次回答查到第几次起，在结果里提醒模型收尾（不打断、不报错）。10-12 回答评测：对比类问题查了 24 次、180 秒，
# 后面十几次是边看边扩大范围（用户没问的城市、换参数重查统计）
SOFT_LIMIT = 10
SOFT_NOTE = "这次回答已经查了 {n} 次。够回答就直接写；还缺的说清缺什么，不要再扩大范围（用户没问的城市、公司、类型不查）。"
CAP_MESSAGE = ("出错了：这次回答已经调用 {name} {n} 次，不能再调。\n"
               "怎么改：用已经查到的结果回答；没查到的如实说，并告诉用户可以换个更具体的问法。")


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
        self.on_done: Any = None  # 每次调用结束后通知一下（网页显示"查到了多少"）；参数是调用记录和结构化结果
        self.user_request: str | None = None
        self.job_criteria: dict[str, dict[str, Any]] = {}
        self.final_response: FinalResponse | None = None

    def invoke(self, spec: ToolSpec, raw_args: dict[str, Any]) -> str:
        start = time.perf_counter()
        try:
            args = spec.args_schema.model_validate(raw_args)
        except ValidationError as e:
            return self._done(spec, raw_args, validation_error(spec, e).render(), False, "invalid_args", start)
        normalized = args.model_dump(mode="json")
        if not demo_tool_allowed(spec.name, live=spec.live):
            return self._done(spec, normalized, "出错了：合成演示未连接真实平台或知识图谱。\n"
                              "怎么改：只用本地合成岗位和面经，不执行现查。", False, "demo_boundary", start)
        if self.agent == "main" and spec.name == "jobs_search" and self.user_request:
            # 只由当前用户原话授权，来源正文和模型自说的“看更新”不能放宽用户的发布日期要求。
            wants_updated = bool(re.search(r"(?:看|查|找|筛|用|包含|允许)[^。！？;\n]{0,6}(?:更新时间|最近更新|最新更新)|"
                                           r"不限发布时间", self.user_request))
            if re.search(r"(?:不要|不用|不看|不能|别|不允许)[^。！？;\n]{0,8}(?:更新时间|最近更新|最新更新)", self.user_request):
                wants_updated = False
            if normalized.get("date_basis") == "reported" and not wants_updated:
                return self._done(spec, normalized, "出错了：用户没有要求改查更新时间，不能把它冒充发布时间。\n"
                                  "怎么改：用published及原日期窗口；已有excluded_examples会解释排除原因，"
                                  "没有适合岗位就如实说，不用换日期口径凑数。", False, "constraint_violation", start)
            # 只约束当前原话明确且唯一的天数窗口；多窗口比较、未明确天数时不猜。
            windows = re.findall(r"(?:最新|最近|过去|近)\s*(\d{1,3}|[一二两三四五六七八九十])\s*天", self.user_request)
            digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                      "七": 7, "八": 8, "九": 9, "十": 10}
            days = {int(n) if n.isdigit() else digits[n] for n in windows}
            can_widen = bool(re.search(r"(?:允许|可以|可)[^。！？;\n]{0,4}放宽|不限(?:时间|日期)", self.user_request))
            if len(days) == 1 and not can_widen and normalized.get("since_days") not in days:
                return self._done(spec, normalized, "出错了：查询天数与用户明确的日期窗口不一致。\n"
                                  "怎么改：保持原窗口，不移除日期条件、不扩大到一周或更长；"
                                  "不足就解释排除原因。", False, "constraint_violation", start)
        if self.agent == "main":  # 和用户对话时的刹车；评测和 MCP 照旧
            same = next((c for c in reversed(self.calls) if c["tool"] == spec.name and c["args"] == normalized and c["ok"]), None)
            if same is not None and spec.name == "jobs_get":
                # 同一岗位刚被换城市/出勤条件重新查询过，不能复用旧范围的详情。
                try:
                    cached_scope = (json.loads(same["output"]).get("match") or {}).get("criteria") or {}
                except (TypeError, ValueError, AttributeError):
                    cached_scope = None
                if cached_scope != self._job_criteria(normalized.get("job_id")):
                    same = None
            if same is not None and not spec.live:  # 参数一模一样、这次回答里刚查过：直接给上次的结果
                return same["output"]
            cap = PER_ANSWER_CAPS.get(spec.name)
            if cap is not None and sum(1 for c in self.calls if c["tool"] == spec.name) >= cap:
                return self._done(spec, normalized, CAP_MESSAGE.format(name=spec.name, n=cap), False, "per_answer_cap", start)
        if spec.live:
            from polaris.tools.live import LIVE_PER_ANSWER

            if sum(1 for c in self.calls if c.get("live")) >= LIVE_PER_ANSWER:
                return self._done(spec, normalized, f"出错了：这次回答已经现查了 {LIVE_PER_ANSWER} 次，不能再查。\n"
                                  "怎么改：用已经查到的和本地库回答，告诉用户还想查什么可以下一句再说。", False, "live_cap", start)

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
            if spec.name == "jobs_search" and isinstance(data, dict):
                from polaris.matching_scope import criteria

                current = criteria(normalized)
                for item in data.get("items", []):
                    if isinstance(item, dict) and isinstance(item.get("id"), str):
                        self.job_criteria[item["id"]] = current.copy()
            elif spec.name == "jobs_get" and isinstance(data, dict):
                current = self._job_criteria(data.get("id"))
                if current:
                    from polaris.match import matches

                    with session_scope(self.engine) as s:
                        fit = matches(s, [int(data["id"][2:])], criteria=current)
                    if int(data["id"][2:]) in fit:
                        data["match"] = fit[int(data["id"][2:])]
            if self.agent == "main" and isinstance(data, dict) and len(self.calls) + 1 >= SOFT_LIMIT:
                data = {**data, "budget": SOFT_NOTE.format(n=len(self.calls) + 1)}
            if spec.return_direct and isinstance(data, FinalResponse):
                self.final_response = data
                output, ok, kind = data.answer, data.error is None, "report_incomplete" if data.error else None
                data = {"status": data.status}
            else:
                output, ok, kind = render(data, spec.max_output_chars), True, None
        except ToolError as e:
            data = None
            output, ok, kind = e.render(), False, "tool_error"
        except Exception as e:  # noqa: BLE001  工具自己的 bug 不能让 agent 崩掉
            data = None
            output, ok, kind = INTERNAL_ERROR.format(name=type(e).__name__), False, "internal"
        if self.cassette is not None:
            self.cassette.record_tool(spec.name, normalized, output, ok, kind)
        return self._done(spec, normalized, output, ok, kind, start, data=data)

    def _job_criteria(self, reference: str | None) -> dict[str, Any]:
        parsed = re.fullmatch(r"\s*(?:J-)?(\d+)\s*", reference or "", re.I)
        if not parsed:
            return {}
        reference = f"J-{int(parsed.group(1))}"
        if reference in self.job_criteria:
            return self.job_criteria[reference]
        if self.agent != "main" or not self.thread_id:
            return {}
        from sqlalchemy import select

        from polaris.db.models import ChatMessage
        from polaris.matching_scope import criteria

        with session_scope(self.engine) as s:
            previous = s.scalars(select(ChatMessage).where(ChatMessage.thread_id == self.thread_id,
                                                          ChatMessage.role == "assistant")
                                 .order_by(ChatMessage.id.desc()).limit(20))
            for message in previous:
                for item in (message.turn or {}).get("results", []):
                    if item.get("id") == reference:
                        return criteria((item.get("match") or {}).get("criteria") or {})
        return {}

    def _done(self, spec: ToolSpec, args: dict[str, Any], output: str, ok: bool, kind: str | None,
              start: float, replayed: bool = False, data: Any = None) -> str:
        row = {"run_id": self.run_id, "thread_id": self.thread_id, "agent": self.agent, "tool": spec.name,
               "args": args, "output": output, "ok": ok, "error_kind": kind,
               "latency_ms": int((time.perf_counter() - start) * 1000), "replayed": replayed}
        self.calls.append({**row, "live": spec.live, "started": start})
        if data is not None:
            self.results[len(self.calls) - 1] = data
        if self.record_db:
            with session_scope(self.engine) as s:
                s.add(ToolCall(**row))
        if self.on_done is not None:
            try:
                self.on_done(row, data)
            except Exception:  # noqa: BLE001  显示进度出错不能影响工具结果
                pass
        return output

    def as_langchain_tool(self, spec: ToolSpec) -> StructuredTool:
        """给 LangChain agent 用。参数 schema 以 JSON Schema 形式交给模型，校验留在 invoke 里做，
        这样参数不合格的调用也会被记账，模型也能拿到"怎么改"的提示。"""
        def run(**kwargs: Any) -> str:
            return self.invoke(spec, kwargs)

        schema = spec.args_schema.model_json_schema()
        return StructuredTool.from_function(func=run, name=spec.name, description=spec.description,
                                            args_schema=schema, infer_schema=False, return_direct=spec.return_direct)

    def langchain_tools(self, specs: list[ToolSpec]) -> list[StructuredTool]:
        return [self.as_langchain_tool(s) for s in specs]
