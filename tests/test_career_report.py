"""纯合成证据：拒绝伪造引用／范围串用，关键事实不由模型补写。"""
import copy
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from polaris.agent.career_report import (
    CareerEvidenceSelection,
    CareerReportPlan,
    PreparationNotes,
    ReportEvidenceError,
    plan_prompt,
    preparation_prompt,
    render_report,
    validate_plan,
    validate_selection,
)


@pytest.fixture
def sample():
    scope = {"city": "上海", "direction": "Agent", "job_type": "intern", "max_days_per_week": 4}
    source = {"label": "合成官网", "url": "https://example.com/job", "published_at": "2026-08-25T14:00:00Z",
              "date_kind": "published", "fetched_at": "2026-10-11T02:00:00Z", "availability": "not_checked"}
    first = {"id": "J-1", "company": "合成公司", "title": "Agent实习", "city": "上海、北京",
             "days_per_week": 4, "duration_months": 6}
    second = {**first, "id": "J-2", "title": "Agent后端实习"}
    detail = {**first, "unit": "合成平台组", "requirements": ["本科及以上在读，论文不是必要条件", "Agent工具调用与可靠性评测"],
              "jd_text": "本科及以上在读，论文不是必要条件。工作：Agent工具调用与可靠性评测。", "sources": [source],
              "last_seen": "2026-10-23", "match": {"criteria": scope.copy(), "checks": [
                  {"key": "city", "label": "城市", "ok": True, "detail": "上海"},
                  {"key": "type", "label": "类型", "ok": True, "detail": "实习"},
                  {"key": "days", "label": "天数", "ok": True, "detail": "每周4天"}],
                  "skills": {"not_stated": ["强化学习"]}}}
    question = {"id": "Q-3", "from": "P-4", "question": "如何评测Agent效果？", "evidence": "一面问题：如何评测Agent效果？",
                "post_title": "合成Agent一面与二面面经", "round": None, "team": None, "material": "firsthand",
                "company": "合成公司", "sources": [{"url": "https://example.com/interview"}]}
    search = {"items": [first, second], "pagination": {"shown": 2, "next_offset": 2},
              "search": {"candidates_before_query": 18, "retrieved_candidates": 3, "query_executed": True, "status": "matches"},
              "days_filter": {"jd_without_days": 52}}
    calls = [{"tool": "jobs_search", "args": {**scope, "limit": 2}, "ok": True, "output": search},
             {"tool": "jobs_get", "args": {"job_id": "J-1", "response_format": "detailed"}, "ok": True, "output": detail},
             {"tool": "questions_search", "args": {"query": "Agent", "company": "合成公司"}, "ok": True,
              "output": {"items": [question]}}]
    raw_plan = {"search_call_index": 0, "job_ids": ["J-1", "J-2"], "detail_call_index": 1,
                "question_call_index": 2, "question_id": "Q-3",
                "jd_basis": {"field": "requirements", "requirement_index": 1, "text": "Agent工具调用与可靠性评测"},
                "sourced_answer_points": ["可先列任务成功率、延迟和失败归因。"],
                "self_test_question": "如何为工具调用设计故障注入和恢复验收？",
                "self_test_answer_points": ["可注入超时和响应丢失，分别检查有界重试与幂等。"]}
    return calls, raw_plan


def test_three_counts_and_pre_days_scope_are_rendered_from_tool(sample):
    calls, raw = sample
    text = render_report(CareerReportPlan.model_validate(raw), calls)
    assert "结构化范围候选 18 条；本页返回 2 条；本次列出 2 条" in text
    assert "关键词召回 3 条，是召回子集" in text
    assert "之前、更宽的结构化范围中，有 52 条未写天数" in text
    assert "下一页 offset=2" in text


def test_exportable_report_carries_synthetic_scope_without_relying_on_web_banner(sample):
    calls, raw = sample
    calls[0]["output"]["library"] = {"synthetic": True}
    text = render_report(CareerReportPlan.model_validate(raw), calls)
    assert text.startswith("合成演示：")
    assert "公司名仅为示例标签" in text


def test_parent_title_and_unknown_question_round_are_both_retained(sample):
    calls, raw = sample
    text = render_report(CareerReportPlan.model_validate(raw), calls)
    assert "合成Agent一面与二面面经" in text
    assert "题目定位轮次：未知" in text
    assert "题目级未知不代表整帖未标轮次" in text
    assert "不能仅凭标题给单题补轮次" in text
    calls[2]["output"]["items"][0]["round"] = "2面"
    assert "题目定位轮次：2面" in render_report(CareerReportPlan.model_validate(raw), calls)


def test_no_hc_or_contest_equivalence_and_no_claim_of_user_skill(sample):
    calls, raw = sample
    text = render_report(CareerReportPlan.model_validate(raw), calls)
    assert "原记录城市：上海、北京" in text
    assert "当前各地名额和能否自由选择尚未确认" in text
    assert "某项竞赛是否被雇主认可不能" in text
    assert "实习时长字段为 6 个月" in text and "个人时长是否满足，待核实" in text
    assert "强化学习；未记录不代表不会" in text
    assert "同公司不等于同团队" in text


def test_later_candidate_conflicts_and_unknowns_are_not_hidden_by_first_job(sample):
    calls, raw = sample
    calls[0]["output"]["items"][1]["match"] = {
        "criteria": calls[0]["args"], "score": 95, "eligibility": "incompatible",
        "checks": [{"key": "degree", "label": "学历", "ok": False, "detail": "要求硕士，个人记录为本科"},
                   {"key": "months", "label": "实习时长", "ok": None, "detail": "个人可实习时长尚未记录"}],
    }
    text = render_report(CareerReportPlan.model_validate(raw), calls)
    assert "[J-2] 匹配核对" in text
    assert "学历：明确不符；要求硕士，个人记录为本科" in text
    assert "实习时长：未知；个人可实习时长尚未记录" in text
    assert "存在明确硬门槛冲突，不作为推荐" in text


def test_later_candidate_without_match_is_explicitly_unassessed(sample):
    calls, raw = sample
    text = render_report(CareerReportPlan.model_validate(raw), calls)
    assert "[J-2] 匹配核对：未取得个人门槛核对，不能当作个人符合" in text


def test_later_candidate_cannot_borrow_match_from_another_search_scope(sample):
    calls, raw = sample
    calls[0]["output"]["items"][1]["match"] = {"criteria": {"city": "北京"}, "checks": []}
    with pytest.raises(ReportEvidenceError, match="列表岗位匹配条件"):
        render_report(CareerReportPlan.model_validate(raw), calls)


def test_later_candidate_direction_mismatch_remains_separate_from_hard_rejection(sample):
    calls, raw = sample
    calls[0]["output"]["items"][1]["match"] = {
        "criteria": calls[0]["args"], "checks": [
            {"key": "role", "label": "方向", "ok": False, "detail": "岗位方向与当前意向不一致"}],
    }
    text = render_report(CareerReportPlan.model_validate(raw), calls)
    assert "方向：明确不符；岗位方向与当前意向不一致" in text
    assert "存在明确硬门槛冲突" not in text


def test_dates_do_not_turn_active_into_live_recruitment(sample):
    calls, raw = sample
    calls[1]["output"]["status"] = "active"
    text = render_report(CareerReportPlan.model_validate(raw), calls)
    assert "来源发布日期=2026-08-25T14:00:00Z" in text
    assert "本份原文快照保存时刻=2026-10-11T02:00:00Z" in text
    assert "最近再次识别日期=2026-10-23" in text
    assert "不保证首次上线" in text and "不等于原文更新或现在仍在招" in text


def test_structured_shape_cannot_accept_model_supplied_counts_or_qualification(sample):
    _, raw = sample
    for key, value in [("candidate_count", 2), ("eligible", True), ("round", "1面"), ("current_hc", 2)]:
        with pytest.raises(ValidationError):
            CareerReportPlan.model_validate({**raw, key: value})


@pytest.mark.parametrize("mutation", [
    lambda c, p: p.update(job_ids=["J-999"]),
    lambda c, p: p.update(job_ids=["J-1", "J-1"]),
    lambda c, p: p.update(job_ids=["J-2", "J-1"]),
    lambda c, p: p.update(detail_call_index=99),
    lambda c, p: c[1].update(ok=False),
    lambda c, p: c[1]["output"].update(id="J-2"),
    lambda c, p: c[1]["output"]["match"]["criteria"].update(city="北京"),
    lambda c, p: c[1]["args"].update(response_format="concise"),
    lambda c, p: c[1]["output"]["match"]["checks"].append({"key": "degree", "ok": False}),
    lambda c, p: p.update(question_id="Q-999"),
    lambda c, p: c[2]["output"]["items"][0].update(material="study"),
    lambda c, p: c[2]["output"]["items"][0].update(evidence=None),
    lambda c, p: c[2]["output"]["items"][0].update(company="另一合成公司"),
    lambda c, p: p["jd_basis"].update(text="原文并没有这一条要求"),
    lambda c, p: p["jd_basis"].update(requirement_index=99),
    lambda c, p: p.update(sourced_answer_points=[]),
    lambda c, p: p.update(self_test_answer_points=[" "]),
    lambda c, p: c[0]["output"]["pagination"].update(shown=1),
    lambda c, p: c[0]["output"]["search"].update(candidates_before_query=1),
])
def test_missing_cross_scope_or_fabricated_evidence_is_rejected(sample, mutation):
    calls, raw = copy.deepcopy(sample)
    mutation(calls, raw)
    with pytest.raises(ReportEvidenceError):
        validate_plan(CareerReportPlan.model_validate(raw), calls)


def empty_case(sample, status, total=0, recalled=0, query_executed=False):
    calls, _ = copy.deepcopy(sample)
    output = calls[0]["output"]
    output.update(items=[], pagination={"shown": 0, "next_offset": None})
    output["search"].update(status=status, candidates_before_query=total,
                            retrieved_candidates=recalled, query_executed=query_executed)
    return calls, CareerReportPlan(search_call_index=0, job_ids=[])


def test_empty_scope_stops_without_fabricated_detail_and_query_not_executed(sample):
    calls, plan = empty_case(sample, "scope_empty")
    text = render_report(plan, calls)
    assert "结构化范围候选 0 条" in text and "当前本地结构化范围为空" in text
    assert "未执行关键词召回" in text and "不扩大条件" in text
    assert "自测题" not in text and "[J-" not in text
    with pytest.raises(ReportEvidenceError):
        render_report(plan.model_copy(update={"question_id": "Q-3"}), calls)


@pytest.mark.parametrize("status,recalled,expected", [
    ("retrieval_miss", 0, "关键词召回未命中，不能说没有岗位"),
    ("page_exhausted", 3, "本页已越过召回末尾，不能说没有岗位"),
])
def test_empty_page_is_not_empty_scope(sample, status, recalled, expected):
    calls, plan = empty_case(sample, status, total=18, recalled=recalled, query_executed=True)
    text = render_report(plan, calls)
    assert "结构化范围候选 18 条" in text and expected in text
    assert "当前本地结构化范围为空" not in text


def test_invalid_json_or_failed_tools_cannot_be_used(sample):
    calls, raw = sample
    calls[1]["output"] = "{"
    with pytest.raises(ReportEvidenceError, match="完整JSON"):
        render_report(CareerReportPlan.model_validate(raw), calls)
    prompt = plan_prompt("合成任务", [{"tool": "jobs_search", "ok": False, "output": "FAILED"}])
    assert "FAILED" not in prompt


def test_exact_full_jd_anchor_and_json_tool_outputs(sample):
    calls, raw = sample
    raw["jd_basis"].update(field="jd_text", requirement_index=None, text="Agent工具调用与可靠性评测")
    for c in calls:
        c["output"] = json.dumps(c["output"], ensure_ascii=False)
    text = render_report(CareerReportPlan.model_validate(raw), calls)
    assert "JD依据" in text and "不是面经原题" in text
    assert "不能当原帖标准答案或个人做过的经历" in text


def test_preparation_input_only_contains_bound_question_and_jd_quote(sample):
    calls, raw = sample
    calls[1]["output"]["jd_text"] += " 不应传给建议生成的秘密项目段落"
    selection = CareerEvidenceSelection.model_validate({k: raw[k] for k in CareerEvidenceSelection.model_fields})
    prompt = preparation_prompt(selection, calls)
    assert "如何评测Agent效果？" in prompt and "Agent工具调用与可靠性评测" in prompt
    for absent in ["秘密项目", "合成公司", "J-1", "上海", "2026-", "合成Agent一面与二面面经", "强化学习"]:
        assert absent not in prompt
    with pytest.raises(ValidationError):
        PreparationNotes.model_validate({"city": "上海", "current_hc": True})
    combined = CareerReportPlan.model_validate({**selection.model_dump(), **PreparationNotes(
        sourced_answer_points=["可逐项定义任务成功与失败"], self_test_question="如何验收工具故障恢复？",
        self_test_answer_points=["可验证超时后是否保持幂等"]).model_dump()})
    assert "任务成功与失败" in render_report(combined, calls)


def test_empty_scope_never_needs_preparation_generation(sample):
    calls, plan = empty_case(sample, "scope_empty")
    with pytest.raises(ReportEvidenceError, match="空结果无需"):
        preparation_prompt(plan, calls)


@pytest.mark.parametrize("key", ["city", "days", "type"])
def test_unknown_requested_hard_condition_cannot_be_selected_as_explicit_fit(sample, key):
    calls, raw = sample
    for check in calls[1]["output"]["match"]["checks"]:
        if check["key"] == key:
            check["ok"] = None
    with pytest.raises(ReportEvidenceError, match="尚未明确符合"):
        render_report(CareerReportPlan.model_validate(raw), calls)


def test_new_counts_and_unknown_date_kind_do_not_reuse_fixture_conclusions(sample):
    calls, raw = sample
    calls[0]["output"]["search"].update(candidates_before_query=73, retrieved_candidates=11)
    calls[0]["output"]["days_filter"]["jd_without_days"] = 9
    source = calls[1]["output"]["sources"][0]
    source["date_kind"] = "updated"
    text = render_report(CareerReportPlan.model_validate(raw), calls)
    assert "结构化范围候选 73 条" in text and "关键词召回 11 条" in text
    assert "有 9 条未写天数" in text and "来源更新时间=" in text
    assert "来源发布日期=" not in text
    source.pop("date_kind")
    assert "性质未确认的来源日期=" in render_report(CareerReportPlan.model_validate(raw), calls)


def test_personal_requirement_review_is_kept_in_report_without_invented_qualification(sample):
    from polaris.db.models import Job
    from polaris.match import job_match
    from polaris.proactive.decide import Profile

    calls, raw = sample
    detail = calls[1]["output"]
    j = Job(title="合成Agent实习", requirements=detail["requirements"], duration_months=6)
    reviews = job_match(j, Profile(), background={"degree": "本科在读", "paper_experience": "无论文"})["requirements_review"]
    detail["match"]["requirements_review"] = reviews
    answer = render_report(CareerReportPlan.model_validate(raw), calls)
    assert "学历解释：个人已记录情况=本科在读" in answer
    assert "论文解释：个人已记录情况=无论文" in answer
    assert "论文非必要" in answer and "时长未记录" in answer


def selection_of(raw):
    return CareerEvidenceSelection.model_validate({k: raw[k] for k in CareerEvidenceSelection.model_fields})


class Extractor:
    def __init__(self, values, *, after=None, failure=None):
        self.values, self.after, self.failure, self.calls = iter(values), after, failure, []

    def extract(self, prompt, schema, **kwargs):
        self.calls.append((prompt, schema, kwargs))
        if self.after:
            self.after()
        if self.failure:
            raise RuntimeError(self.failure)
        value = next(self.values)
        return SimpleNamespace(ok=value is not None, value=value, error="sk-must-not-leak", call_ids=[len(self.calls)])


def notes_of(raw):
    return PreparationNotes.model_validate({k: raw[k] for k in PreparationNotes.model_fields})


def test_partial_report_keeps_synthetic_scope_when_preparation_fails(sample):
    from polaris.agent.report_workflow import produce_report

    calls, raw = sample
    calls[0]["output"]["library"] = {"synthetic": True}
    result = produce_report("合成完整请求", calls, selection=selection_of(raw), llm=Extractor([None]))
    assert result.status == "insufficient_evidence" and result.answer.startswith("合成演示：")
    assert "这次完整报告未完成" in result.answer and "公司名仅为示例标签" in result.answer


def test_stop_during_invalid_preparation_is_not_delivered_as_partial_report(sample):
    from polaris.agent.report_workflow import produce_report

    calls, raw = sample
    stopped = []
    model = Extractor([None], after=lambda: stopped.append(True))
    result = produce_report("合成完整请求", calls, selection=selection_of(raw), llm=model,
                            should_stop=lambda: bool(stopped))
    assert result.status == "stopped" and result.answer == ""
    assert result.llm_call_ids == [1]
    assert len(model.calls) == 1


def test_workflow_is_bounded_and_preserves_two_independent_outlines(sample):
    from polaris.agent.report_workflow import produce_report

    calls, raw = sample
    model = Extractor([selection_of(raw), notes_of(raw)])
    result = produce_report("合成完整任务", calls, llm=model)
    assert result.status == "ready" and result.error is None
    assert result.llm_call_ids == [1, 2] and len(model.calls) == 2
    assert "结构化范围候选 18 条" in result.answer and "故障注入" in result.answer
    assert all(c[2]["max_fix"] == 0 and c[2]["cache"] is False for c in model.calls)
    # 建议生成没有接收主请求、原帖轮次、个人信息或其余JD。
    assert "合成完整任务" not in model.calls[-1][0] and "上海" not in model.calls[-1][0]


def test_bad_selection_stops_before_model_and_never_adopts_earlier_draft(sample):
    from polaris.agent.report_workflow import produce_report

    calls, raw = sample
    model = Extractor([])
    selection = selection_of(raw).model_copy(update={"question_id": "Q-999"})
    result = produce_report("合成任务", calls, llm=model, selection=selection)
    assert result.status == "insufficient_evidence" and result.error
    assert model.calls == [] and "完整报告未完成" in result.answer
    assert "Q-999" not in result.answer and "[J-1]" in result.answer


@pytest.mark.parametrize("failure", [False, True])
def test_schema_or_service_failure_is_explicit_and_not_retried(sample, failure):
    from polaris.agent.report_workflow import produce_report

    calls, _ = sample
    model = Extractor([None], failure="sk-private-key-in-exception" if failure else None)
    result = produce_report("合成任务", calls, llm=model)
    assert result.status == ("model_error" if failure else "insufficient_evidence")
    assert result.error and len(model.calls) == 1
    assert "sk-" not in result.error + result.answer


def test_stop_before_and_during_selection_prevents_following_calls(sample):
    from polaris.agent.report_workflow import produce_report

    calls, raw = sample
    model = Extractor([])
    result = produce_report("合成任务", calls, llm=model, should_stop=lambda: True)
    assert result.status == "stopped" and result.answer == "" and model.calls == []
    stopped = []
    model = Extractor([selection_of(raw)], after=lambda: stopped.append(True))
    result = produce_report("合成任务", calls, llm=model, should_stop=lambda: bool(stopped))
    assert result.status == "stopped" and result.answer == "" and len(model.calls) == 1


def test_stop_during_preparation_does_not_deliver_generated_answer(sample):
    from polaris.agent.report_workflow import produce_report

    calls, raw = sample
    stopped = []
    model = Extractor([notes_of(raw)], after=lambda: stopped.append(True))
    result = produce_report("合成任务", calls, llm=model, selection=selection_of(raw), should_stop=lambda: bool(stopped))
    assert result.status == "stopped" and result.answer == "" and result.llm_call_ids == [1]


def test_empty_search_with_selection_requires_no_model(sample):
    from polaris.agent.report_workflow import produce_report

    calls, plan = empty_case(sample, "scope_empty")
    model = Extractor([])
    result = produce_report("合成空结果", calls, llm=model, selection=plan)
    assert result.status == "ready" and model.calls == [] and result.llm_call_ids == []
    assert "结构化范围候选 0 条" in result.answer


def test_stable_id_resolution_uses_actual_completion_order_and_refuses_scope_switch(sample):
    from polaris.agent.report_workflow import ReportRequest, produce_report, resolve_selection

    calls, raw = sample
    request = ReportRequest(job_ids=raw["job_ids"], question_id=raw["question_id"], jd_basis=raw["jd_basis"])
    reversed_calls = [calls[2], calls[0], calls[1]]
    selection = resolve_selection(request, reversed_calls)
    assert (selection.search_call_index, selection.detail_call_index, selection.question_call_index) == (1, 2, 0)
    validate_selection(selection, reversed_calls)
    other = copy.deepcopy(calls[0])
    other["args"]["city"] = "北京"
    reversed_calls.append(other)
    result = produce_report("合成任务", reversed_calls, selection=resolve_selection(request, reversed_calls), llm=Extractor([]))
    assert result.status == "insufficient_evidence" and "条件" in result.error


def test_direct_final_response_is_not_cut_at_search_result_limit():
    from pydantic import BaseModel

    from polaris.tools.base import FinalResponse, ToolSpec
    from polaris.tools.runtime import ToolRuntime

    class Args(BaseModel):
        pass

    text = "合成报告" * 2000
    spec = ToolSpec("synthetic_report", "合成收尾", Args, lambda a: FinalResponse(text, "ready"), return_direct=True)
    runtime = ToolRuntime(record_db=False)
    assert runtime.as_langchain_tool(spec).return_direct is True
    assert runtime.invoke(spec, {}) == text
    assert runtime.final_response.answer == text


def test_default_report_client_is_bounded_and_records_actual_usage(sample, monkeypatch):
    from polaris.agent import report_workflow as module
    from polaris.llm.recorder import MemoryRecorder

    calls, raw = sample
    captured = {}

    class Client:
        def __init__(self, settings, recorder):
            captured["settings"] = settings
            self.recorder = recorder

        def extract(self, prompt, schema, **kwargs):
            cid = self.recorder.record({"model": "synthetic", "latency_ms": 20, "prompt_tokens": 10,
                                        "completion_tokens": 5, "cost_usd": None})
            return SimpleNamespace(ok=True, value=notes_of(raw), call_ids=[cid])

    monkeypatch.setattr(module, "LLM", Client)
    result = module.produce_report("合成任务", calls, selection=selection_of(raw), recorder=MemoryRecorder())
    assert result.status == "ready" and result.llm_call_ids == [1]
    assert result.model_trace[0]["input_tokens"] == 10 and result.model_trace[0]["cost_usd"] is None
    assert captured["settings"].llm_max_retries == 0 and captured["settings"].llm_timeout == 45
    assert captured["settings"].llm_json_mode == "json_object"


def test_direct_report_is_saved_as_final_ai_message_and_prep_usage_is_in_turn(db, sample, monkeypatch):
    from datetime import UTC, datetime
    from uuid import uuid4

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from polaris.agent import report_workflow as module
    from polaris.agent.main import chat, checkpointer
    from polaris.agent.transcript import visible_messages
    from polaris.db.models import InterviewQuestion, Job, Post, RawItem
    from polaris.db.session import session_scope
    from polaris.tools import REGISTRY

    calls, raw = sample
    now = datetime.now(UTC)
    with session_scope() as s:
        for jid in (1, 2):
            s.add(Job(id=jid, key=f"final-report-{jid}", title="合成Agent实习", company="合成公司", city="上海",
                      cities=["上海"], job_type="intern", days_per_week=4, duration_months=6, requirements=["本科及以上在读"],
                      first_seen_at=now, last_seen_at=now))
        s.add(RawItem(id=1, source="manual", kind="post", raw_text="合成一面\n如何评测Agent效果？", raw_json={},
                      fetched_at=now, content_hash="report-integration"))
        s.flush()
        s.add(Post(id=4, raw_item_id=1, post_type="面经", summary="合成一面"))
        s.flush()
        s.add(InterviewQuestion(id=3, post_id=4, key="report-question", company="合成公司", question="如何评测Agent效果？",
                                category="Agent", material="firsthand", attribution="explicit", evidence="如何评测Agent效果？"))
    for c in calls:
        monkeypatch.setattr(REGISTRY[c["tool"]], "func", lambda args, out=c["output"]: copy.deepcopy(out))

    class Client:
        def __init__(self, settings, recorder):
            self.recorder = recorder

        def extract(self, prompt, schema, **kwargs):
            cid = self.recorder.record({"purpose": "career_report:preparation", "model": "synthetic-prep", "endpoint": "chat",
                                        "provider": "synthetic", "request": {}, "latency_ms": 20,
                                        "prompt_tokens": 10, "completion_tokens": 5})
            return SimpleNamespace(ok=True, value=notes_of(raw), call_ids=[cid])

    monkeypatch.setattr(module, "LLM", Client)
    class Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    scripted = [AIMessage(content="", tool_calls=[{"id": str(i), "name": c["tool"], "args": c["args"]}]) for i, c in enumerate(calls)]
    scripted.append(AIMessage(content="", tool_calls=[{"id": "final", "name": "career_report", "args": {
        "job_ids": raw["job_ids"], "question_id": raw["question_id"], "jd_basis": raw["jd_basis"]}}]))
    tid = f"report-{uuid4()}"
    turn = chat("合成完整求职报告", tid, model=Model(messages=iter(scripted)),
                remember_fn=lambda *a, **kw: {"events": [], "questions": []})
    assert turn.error is None
    assert "结构化范围候选 18 条" in turn.answer and "故障注入" in turn.answer
    assert turn.tool_calls[-1]["tool"] == "career_report"
    assert turn.trace["totals"]["model_calls"] == len(scripted) + 1
    assert len(turn.llm_call_ids) == len(scripted) + 1
    assert turn.trace["model_calls"][-1]["model"] == "synthetic-prep"
    saved = checkpointer().get_tuple({"configurable": {"thread_id": tid}}).checkpoint["channel_values"]["messages"]
    assert saved[-1].type == "ai" and saved[-1].content == turn.answer
    assert saved[-2].type == "tool" and "实际交付见下一条" in saved[-2].content
    with session_scope() as s:
        readback = visible_messages(s, tid)[-1]
        assert readback["content"] == turn.answer and readback["turn"]["llm_call_ids"] == turn.llm_call_ids
