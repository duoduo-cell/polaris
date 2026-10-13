"""验证评测不会把没审完、虚构证据或调用了错误筛选的任务误报通过。"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from polaris.eval import real_tasks as ev


def audit(**updates):
    return {"review_complete": True, "reasonable_tools": True, "resolves_request": True,
            "usefulness": "actionable", "recommended_ids": [], "claims": [], "issues": [], **updates}


def test_honesty_cannot_be_averaged_away_and_incomplete_review_never_passes():
    checks = [{"name": "execution_completed", "ok": True}]
    claim = {"quote": "你没有实习", "verdict": "unsupported", "evidence": []}
    r = ev.finalize(checks, audit(claims=[claim]), None, {})
    assert r["gates"]["rules"] and not r["gates"]["facts"] and not r["pass"]
    assert not ev.finalize(checks, audit(review_complete=False), None, {})["pass"]
    assert not ev.finalize(checks, audit(validation_errors=["source_quote_not_found"]), None, {})["pass"]


def test_judge_quotes_must_exist_in_answer_and_original_source():
    a = audit(claims=[{"quote": "你没有实习", "verdict": "supported",
                       "evidence": [{"source_id": "R-1", "text": "本人有腾讯一段实习"}]}])
    assert ev.validate_audit(a, "你没有实习", {"R-1": {"text": "本人有腾讯一段实习"}}) == []
    assert "source_quote_not_found" in ev.validate_audit(a, "你没有实习", {"R-1": {"text": "论文没提"}})
    assert "claim_not_in_answer" in ev.validate_audit(a, "待确认", {"R-1": {"text": "本人有腾讯一段实习"}})
    assert "supported_without_quote" in ev.validate_audit(audit(claims=[{
        "quote": "你没有实习", "verdict": "supported", "evidence": []}]), "你没有实习", {})


def test_locator_restores_original_lines_and_rejects_out_of_range_proof():
    sources = {"R-1": {"text": "标题\n原文要求至少发表一篇论文", "raw_json": {"date": None}}}
    a = audit(claims=[{"answer_line": 1, "quote": "", "verdict": "supported",
                       "evidence": [{"source_id": "R-1", "field": "text", "line": 1, "text": ""}]}])
    assert ev.resolve_locators(a, "结论\n至少要求一篇论文", sources) == []
    assert a["claims"][0]["quote"] == "至少要求一篇论文"
    assert ev.validate_audit(a, "结论\n至少要求一篇论文", sources) == []
    a["claims"][0]["evidence"][0]["line"] = 99
    assert "invalid_source_locator" in ev.resolve_locators(a, "结论\n至少要求一篇论文", sources)


def test_bounded_judge_cannot_invent_source_or_answer_line():
    from pydantic import ValidationError

    schema, proofs = ev.bounded_audit("\n至少要求一篇论文", {"R-1": {"text": "要求至少一篇论文"}})
    value = audit(reasoning="原文核查", claims=[{"answer_line": 1, "verdict": "supported", "evidence_refs": ["R-1:text:0"]}])
    assert schema.model_validate(value).claims[0].answer_line == 1
    assert proofs["R-1:text:0"]["text"] == "要求至少一篇论文"
    value["claims"][0]["evidence_refs"] = ["J-999:text:0"]
    with pytest.raises(ValidationError):
        schema.model_validate(value)
    value["claims"][0].update(evidence_refs=["R-1:text:0"], answer_line=0)
    with pytest.raises(ValidationError):
        schema.model_validate(value)


def test_calling_peers_with_wrong_cohort_is_not_success():
    case = {"kind": "offer_background", "filters": {"job_type": "实习", "outcome": "拿到offer", "since_days": 30},
            "directions": ["Agent"], "max_tools": 8}
    calls = [{"tool": "peers_search", "args": {"direction": "Agent", "job_type": "校招", "outcome": None,
                                              "since_days": 30}, "ok": True, "output": "{}", "error_kind": None}]
    turn = {"answer": "找到了", "tool_calls": calls}
    checks = ev.hard_checks(case, [turn], calls)
    assert not next(c["ok"] for c in checks if c["name"] == "requested_offer_filters")
    assert not ev.finalize(checks, audit(), None, case)["pass"]


def test_recent_jobs_cannot_pass_by_searching_correct_window_then_widening():
    case = {"kind": "recent_jobs", "filters": {"since_days": 3}, "max_tools": 8}
    calls = [{"tool": "jobs_search", "args": {"since_days": days}, "ok": True,
              "output": '{"items": []}', "error_kind": None} for days in (3, 7)]
    checks = ev.hard_checks(case, [{"answer": "没有匹配", "tool_calls": calls}], calls, [])
    assert next(c["ok"] for c in checks if c["name"] == "requested_job_filters")
    assert not next(c["ok"] for c in checks if c["name"] == "did_not_expand_requested_date_window")


def test_sparse_jobs_does_not_require_three_when_only_one_exists():
    case = {"take": 3}
    r = ev.finalize([], audit(recommended_ids=["J-1"]), ["J-1"], case)
    assert r["pass"]
    assert not ev.finalize([], audit(recommended_ids=["J-1", "J-2", "J-3"]), ["J-1"], case)["pass"]
    # 用户说“最多”不是必须凑数；JD门槛与背景不符时，可以明确解释不推荐。
    assert ev.finalize([], audit(recommended_ids=[]), ["J-1"], case)["pass"]
    assert not ev.finalize([], audit(recommended_ids=[], resolves_request=False), ["J-1"], case)["pass"]


def test_pass_k_counts_all_trials_and_does_not_hide_incomplete_cases():
    def row(case, passed):
        return {"id": case, "pass": passed, "seconds": 1, "tools": 2, "successful_tool_calls": 1, "checks": [], "audit": {}}
    rows = [row("a", True), row("a", True), row("a", False), *[row("b", True) for _ in range(3)], row("c", True)]
    s = ev.summarize(rows, 3)
    assert s["pass^3"] == .5 and s["complete_cases"] == 2 and s["incomplete_cases"] == 1
    assert s["successful_tool_calls"] == 7 and s["passed_runs"] == 6


@pytest.mark.parametrize("db", ["polaris", "polaris_live", "polaris_test", "polaris_eval_tasks_bad"])
def test_real_and_existing_test_databases_are_rejected(monkeypatch, db):
    monkeypatch.setattr(ev, "get_settings", lambda: SimpleNamespace(database_url=f"postgresql://localhost/{db}"))
    with pytest.raises(ValueError):
        ev.guard()


def test_independent_oracle_excludes_future_and_undated_jobs(db):
    from polaris.db.models import Job, Posting, RawItem
    from polaris.db.session import session_scope

    now = datetime(2026, 10, 12, tzinfo=UTC)
    with session_scope() as s:
        for i, posted in enumerate([now.date(), now.date() + timedelta(days=1), None]):
            j = Job(key=f"oracle-{i}", title="Agent开发实习", directions=["Agent"], job_type="intern", status="active",
                    posted_at=posted, first_seen_at=now, last_seen_at=now, is_synthetic=False)
            r = RawItem(source="manual", kind="job", raw_text="合成发布日期",
                        raw_json={"publishDate": posted.isoformat() if posted else None},
                        content_hash=f"oracle-source-{i}", fetched_at=now)
            s.add_all([j, r])
            s.flush()
            s.add(Posting(job_id=j.id, raw_item_id=r.id, posted_at=posted))
    assert ev.job_oracle({"filters": {"direction": "Agent", "job_type": "intern", "since_days": 3}}, now) == ["J-1"]
