"""有界的报告收尾；校验失败交付明确的部分结果，不退回未验证的模型草稿。"""
import json
import re
import time
from collections.abc import Callable
from typing import Any

from pydantic import Field

from polaris.agent.career_report import (
    CareerEvidenceSelection,
    CareerReportPlan,
    JdQuote,
    PreparationNotes,
    ReportEvidenceError,
    StrictModel,
    plan_prompt,
    preparation_prompt,
    render_report,
    validate_selection,
)
from polaris.config import get_settings
from polaris.llm.client import LLM
from polaris.llm.recorder import DbRecorder, Recorder
from polaris.tools.base import FinalResponse, ToolSpec

VERSION = "career-report-v4"


class ReportRequest(StrictModel):
    job_ids: list[str] = Field(max_length=50, description="按本轮搜索结果顺序选择的岗位编号；范围为空时[]")
    question_id: str | None = Field(None, description="本轮已找到的同公司一手面经题编号；空范围时null")
    jd_basis: JdQuote | None = Field(None, description="第一岗的自测题依据，逐字来自该次详情指定字段；空范围时null")


class ReportRecorder:
    def __init__(self, delegate: Recorder, t0: float):
        self.delegate, self.t0 = delegate, t0
        self.ids: list[int | None] = []
        self.trace: list[dict[str, Any]] = []

    def record(self, row: dict[str, Any]) -> int | None:
        call_id = self.delegate.record(row)
        self.ids.append(call_id)
        ms = row.get("latency_ms") or 0
        self.trace.append({"at_ms": max(0, int((time.perf_counter() - self.t0) * 1000) - ms), "ms": ms,
                           "kind": "error" if row.get("error") else "answer", "model": row.get("model"),
                           "input_tokens": row.get("prompt_tokens"), "output_tokens": row.get("completion_tokens"),
                           "reasoning_tokens": row.get("reasoning_tokens"), "cached_tokens": row.get("cached_tokens"),
                           "cost_usd": float(row["cost_usd"]) if row.get("cost_usd") is not None else None})
        return call_id

    def find_cached(self, key: str) -> dict[str, Any] | None:
        return self.delegate.find_cached(key)


def _data(call: dict[str, Any]) -> dict[str, Any]:
    if call.get("ok") is not True:
        return {}
    try:
        value = call.get("output")
        value = json.loads(value) if isinstance(value, str) else value
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def resolve_selection(request: ReportRequest, calls: list[dict[str, Any]]) -> CareerEvidenceSelection:
    """模型用稳定资料编号选证据；并行工具完成顺序不必由模型猜下标。"""
    search = next((i for i in reversed(range(len(calls))) if calls[i].get("tool") == "jobs_search"
                   and (data := _data(calls[i])) and
                   (set(request.job_ids).issubset({x.get("id") for x in data.get("items", [])}) if request.job_ids
                    else not data.get("items"))), None)
    if search is None:
        raise ReportEvidenceError("所选岗位尚无本轮成功搜索依据")
    detail = next((i for i in reversed(range(len(calls))) if calls[i].get("tool") == "jobs_get"
                   and request.job_ids and _data(calls[i]).get("id") == request.job_ids[0]), None)
    question = next((i for i in reversed(range(len(calls))) if calls[i].get("tool") == "questions_search"
                     and request.question_id and any(q.get("id") == request.question_id for q in _data(calls[i]).get("items", []))), None)
    return CareerEvidenceSelection(search_call_index=search, job_ids=request.job_ids, detail_call_index=detail,
                                   question_call_index=question, question_id=request.question_id, jd_basis=request.jd_basis)


def _partial(calls: list[dict[str, Any]], warning: str) -> str:
    lines = ["这次完整报告未完成：" + warning, "以下仅是已取得的本地候选资料，尚未完成全部门槛与面经核对。"]
    for call in reversed(calls):
        if call.get("tool") != "jobs_search" or not (data := _data(call)):
            continue
        meta, page = data.get("search") or {}, data.get("pagination") or {}
        if type(meta.get("candidates_before_query")) is int and type(page.get("shown")) is int:
            lines.append(f"该次结构化范围候选{meta['candidates_before_query']}条，本页返回{page['shown']}条；不代表市场全部岗位。")
        refs = [x["id"] for x in data.get("items", []) if isinstance(x, dict) and re.fullmatch(r"J-\d+", x.get("id", ""))]
        if refs:
            lines.append("已检索资料：" + "、".join(f"[{ref}]" for ref in refs))
        break
    lines.append("没有据此确认当前在招、个人全部达标或具体团队面试归属。")
    return "\n\n".join(lines)


def produce_report(request: str, calls: list[dict[str, Any]], *, selection: CareerEvidenceSelection | None = None,
                   llm: Any = None, recorder: Recorder | None = None, should_stop: Callable[[], bool] | None = None,
                   trace_start: float | None = None) -> FinalResponse:
    """至多选择一次、建议一次；默认客户端无网络重试／格式修补，45秒请求超时。"""
    stop = should_stop or (lambda: False)
    observer = ReportRecorder(recorder or DbRecorder(), trace_start if trace_start is not None else time.perf_counter())
    ids: list[int | None] = []

    def result(answer: str, status: str, error: str | None = None) -> FinalResponse:
        return FinalResponse(answer, status, error, observer.ids or ids, observer.trace)

    def cancelled() -> FinalResponse:
        return result("", "stopped", "你停止了这次回答")

    def extract(prompt: str, schema: Any, purpose: str):
        nonlocal llm
        if llm is None:
            settings = get_settings().model_copy(update={"llm_max_retries": 0, "llm_timeout": 45.0,
                                                          "llm_json_mode": "json_object"})
            llm = LLM(settings=settings, recorder=observer)
        response = llm.extract(prompt, schema, purpose=purpose, role="extract", prompt_version=VERSION,
                               cache=False, max_fix=0, max_tokens=1536)
        ids.extend(response.call_ids)
        if not response.ok or response.value is None:
            raise ReportEvidenceError("模型返回的报告结构不合格，本次没有自动重试或采用草稿")
        return response.value

    try:
        if stop():
            return cancelled()
        if selection is None:
            selection = extract(plan_prompt(request, calls), CareerEvidenceSelection, "career_report:selection")
        if stop():
            return cancelled()
        evidence = validate_selection(selection, calls)
        notes = (extract(preparation_prompt(selection, calls), PreparationNotes, "career_report:preparation")
                 if evidence.jobs else PreparationNotes())
        if stop():
            return cancelled()
        plan = CareerReportPlan.model_validate({**selection.model_dump(), **notes.model_dump()})
        return result(render_report(plan, calls), "ready")
    except ReportEvidenceError as exc:
        if stop():
            return cancelled()
        warning = str(exc)
        return result(_partial(calls, warning), "insufficient_evidence", warning)
    except Exception:  # noqa: BLE001  服务错误只进现有私有调用日志，不把原始异常或密钥发给网页
        if stop():
            return cancelled()
        warning = "模型服务未能完成报告生成，已停止本次收尾"
        return result(_partial(calls, warning), "model_error", warning)


def report_tool(runtime: Any, stop: Callable[[], bool], trace_start: float) -> ToolSpec:
    def finish(args: ReportRequest) -> FinalResponse:
        try:
            selection = resolve_selection(args, runtime.calls)
        except ReportEvidenceError as exc:
            warning = str(exc)
            return FinalResponse(_partial(runtime.calls, warning), "insufficient_evidence", warning)
        return produce_report(runtime.user_request or "", runtime.calls, selection=selection,
                              should_stop=stop, trace_start=trace_start)

    return ToolSpec(
        "career_report", "完成整条求职报告的最后一步：用户要求按条件找岗位、展开第一岗门槛与日期、"
        "并给一道面经原题和一道JD自测题及各自要点时，先取得jobs_search、第一岗detailed的jobs_get、"
        "questions_search，再单独调用此工具直接交付报告，不与其他工具并行，不另写最终结论。"
        "job_ids只能来自本轮同一次搜索且保持原顺序，第一岗必须明确满足本次城市、类型及天数；"
        "question_id选该公司有evidence的一手面经题，jd_basis逐字引用第一岗原文的具体工作或能力。"
        "结构化范围为空时job_ids=[]、question_id=null、jd_basis=null，不查其他方向凑数。"
        "其他单独的查岗、解释或备面试任务按原方式回答，无需此工具。",
        ReportRequest, finish, return_direct=True)
