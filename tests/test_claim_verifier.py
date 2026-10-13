"""事实核验必须覆盖每句且保留初判；局部正确不能让整个任务误报通过。"""
from copy import deepcopy

from polaris.eval.claim_verifier import apply_verification, verification_input
from polaris.eval.real_tasks import finalize


def reviewed():
    return {"review_complete": True, "reasonable_tools": True, "resolves_request": True, "usefulness": "actionable",
            "claims": [{"quote": "伦敦岗发布于今天", "verdict": "supported",
                        "evidence": [{"source_id": "R-1", "text": "地点：伦敦"}]}], "issues": []}


def test_whole_source_retained_and_partial_support_cannot_pass():
    a = reviewed()
    source = {"R-1": {"text": "地点：伦敦\n更新时间：2026-10-12", "raw_json": {"LastUpdateTime": "2026-10-12"}}}
    schema, packet = verification_input(a, source)
    assert packet["original_sources"] == source
    checked = schema.model_validate({"checks": [{"claim_index": 0, "verdict": "contradicted", "reason": "来源只给更新时间"}]})
    assert apply_verification(a, checked.model_dump()) == []
    assert a["claims"][0]["initial_verdict"] == "supported"
    assert not finalize([], a, None, {})["pass"]


def test_omitted_or_repeated_claim_is_incomplete_not_success():
    for rows in ([], [{"claim_index": 0, "verdict": "supported", "reason": ""}] * 2):
        a = reviewed()
        before = deepcopy(a)
        assert apply_verification(a, {"checks": rows}) == ["incomplete_semantic_verification"]
        assert a == before
