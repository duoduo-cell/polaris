"""纯合成证据：拒绝伪造引用／范围串用，关键事实不由模型补写。"""
import copy
import json

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
