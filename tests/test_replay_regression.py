"""回归测试：把仓库里 M2 的录像全部完整回放，答案必须和录制时一字不差。不调任何服务、不花钱。"""
import pytest

from polaris.agent.minimal import ask
from polaris.config import ROOT
from polaris.replay import Cassette

CASSETTES = sorted((ROOT / "eval" / "cassettes" / "m2").glob("*.jsonl"))


@pytest.mark.parametrize("path", CASSETTES, ids=[p.stem for p in CASSETTES])
def test_full_replay_reproduces_recorded_answer(path):
    cassette = Cassette(path, "replay", llm_replay=True)
    if not cassette.meta:
        pytest.skip("旧录像没有元信息")
    expected = cassette.final_answer()
    run = ask(cassette.meta["question"], cassette=cassette, faults=cassette.meta.get("faults") or None,
              record_db=False)
    assert run.error is None
    assert run.answer == expected
    assert all(c["replayed"] or c["error_kind"] == "injected" for c in run.tool_calls)
