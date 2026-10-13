"""去重不能丢掉原始证据，不能改变实际调用参数和失败记录。"""
import pytest

from polaris.eval.audit_packets import review_packet, review_refs


def test_raw_proofs_are_complete_and_calls_keep_scope_and_failures():
    proofs = {"R-1:text:0": {"text": "硕士及以上", "source_id": "R-1", "field": "text", "line": 0},
              "R-1:raw_json": {"text": '{"updateTime":"2026-10-12"}', "source_id": "R-1", "field": "raw_json"}}
    calls = [{"tool": "jobs_search", "args": {"since_days": 7}, "ok": False, "error_kind": "constraint_violation",
              "output": "不允许扩大窗口"},
             {"tool": "jobs_get", "args": {"id": "J-1"}, "ok": True,
              "output": {"jd_text": "硕士及以上", "requirements": ["硕士及以上"], "sources": [{"raw_id": "R-1"}]}}]
    packet = {"proof_refs": proofs, "tool_calls": calls, "answer_lines": [{"line": 0, "text": "学历不符"}],
              "original_sources": {"R-1": {"text": "硕士及以上"}}, "source_lines": {}, "dialogue": []}
    view = review_packet(packet)
    assert view["proof_refs"] == proofs
    assert view["answer_lines"] == packet["answer_lines"]
    assert view["tool_calls"][0] == calls[0]
    assert view["tool_calls"][1]["args"] == calls[1]["args"]
    assert view["tool_calls"][1]["output"]["sources"] == calls[1]["output"]["sources"]
    assert "jd_text" not in view["tool_calls"][1]["output"]
    assert "original_sources" not in view
    assert "jd_text" in packet["tool_calls"][1]["output"]  # 保存的完整材料不变


def test_no_original_evidence_cannot_be_reviewed_from_tool_summaries():
    with pytest.raises(ValueError):
        review_packet({"tool_calls": []})


def test_excluded_job_without_final_id_and_all_read_sources_are_kept():
    calls = [{"tool": "jobs_search", "ok": True, "output":
              '{"items":[{"id":"J-1"}],"excluded_examples":[{"id":"J-2","sources":[{"raw_id":"R-2"}]}]}'},
             {"tool": "jobs_get", "ok": True, "output": '{"id":"J-3","sources":[{"raw_id":"R-3"}]}'},
             {"tool": "peers_search", "ok": True, "output": '{"items":[{"id":"R-4"},{"id":"R-5"}]}'},
             {"tool": "posts_get", "ok": False, "output": '{"id":"P-6"}'}]
    assert review_refs("暂无适合；面经P-7仅供参考", calls) == {"J-2", "R-2", "J-3", "R-3", "R-4", "R-5", "P-7"}
