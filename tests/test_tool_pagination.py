"""分页/截断不能把展示数变成总数，也不能丢失尚未展示的候选。"""
import json

import pytest

from polaris.tools.base import render


def payload(*, size=20, chars=700, offset=20, next_offset=40):
    return {"items": [{"id": f"J-{i}", "title": "合成" * chars,
                       "sources": [{"url": f"https://example.com/jobs/{i}"}]} for i in range(size)],
            "returned": size, "offset": offset, "next_offset": next_offset,
            "pagination": {"candidate_count": 119, "scope_candidate_count": 119,
                           "count_basis": "本地结构化范围全量候选"}}


def test_truncation_keeps_candidate_total_and_a_continuation_for_every_omitted_item():
    data = payload()
    out = json.loads(render(data))
    assert out["truncated"] and 0 < len(out["items"]) < 20
    assert out["pagination"]["candidate_count"] == 119
    assert out["pagination"]["shown"] == out["returned"] == len(out["items"])
    assert out["next_offset"] == out["pagination"]["next_offset"] == 20 + len(out["items"])
    assert list(out)[0] == "pagination"
    assert out["items"][0]["sources"][0]["url"] == "https://example.com/jobs/0"
    assert len(data["items"]) == 20 and "shown" not in data["pagination"]


@pytest.mark.parametrize("size,next_offset", [(0, None), (1, None), (2, 2)])
def test_untruncated_results_do_not_infer_unknown_totals(size, next_offset):
    out = json.loads(render({"items": [{"id": i} for i in range(size)], "offset": 0, "next_offset": next_offset}))
    assert out["returned"] == out["pagination"]["shown"] == size
    assert out["pagination"]["next_offset"] == next_offset
    assert "candidate_count" not in out["pagination"]


def test_truncated_last_page_still_has_a_continuation():
    out = json.loads(render(payload(next_offset=None)))
    assert out["next_offset"] == 20 + len(out["items"])
    assert out["pagination"]["candidate_count"] == 119


def test_final_compaction_retains_count_basis_and_consistent_page_state():
    data = payload(chars=8000, size=5)
    data["advice"] = "长正文" * 5000
    out = json.loads(render(data))
    assert out["pagination"]["candidate_count"] == 119
    assert out["pagination"]["count_basis"] == "本地结构化范围全量候选"
    assert out["returned"] == out["pagination"]["shown"] == len(out["items"])
    assert len(render(data)) <= 6000


def test_legacy_payload_without_offset_keeps_its_original_page_position():
    data = payload()
    del data["offset"]
    out = json.loads(render(data))
    assert out["pagination"]["offset"] == 20
    assert out["next_offset"] == 20 + len(out["items"])
