"""回归测试：把录像全部完整回放，答案必须和录制时一字不差。不调任何服务、不花钱。

M2 的录像在仓库里（合成数据）；M3 的录像只查官网岗位的在仓库里，查过小红书笔记的只在作者本机 data/ 下，有就一起回放。
"""
import pytest

from polaris.agent.minimal import MINIMAL, SEARCH, ask
from polaris.config import ROOT
from polaris.replay import Cassette

PROFILES = {"minimal": MINIMAL, "search": SEARCH}
CASSETTES = sorted([*(ROOT / "eval" / "cassettes" / "m2").glob("*.jsonl"),
                    *(ROOT / "eval" / "cassettes" / "m3").glob("*.jsonl"),
                    *(ROOT / "data" / "cassettes" / "m3").glob("*.jsonl")])


@pytest.mark.parametrize("path", CASSETTES, ids=[f"{p.parent.name}/{p.stem}" for p in CASSETTES])
def test_full_replay_reproduces_recorded_answer(path):
    cassette = Cassette(path, "replay", llm_replay=True)
    if not cassette.meta:
        pytest.skip("旧录像没有元信息")
    expected = cassette.final_answer()
    run = ask(cassette.meta["question"], cassette=cassette, faults=cassette.meta.get("faults") or None,
              record_db=False, profile=PROFILES[cassette.meta.get("profile", "minimal")])
    assert run.error is None
    assert run.answer == expected
    assert all(c["replayed"] or c["error_kind"] == "injected" for c in run.tool_calls)
