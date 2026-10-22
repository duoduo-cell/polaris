"""未知模型定价或失败调用的未知用量不能在汇总中变成免费。"""
from types import SimpleNamespace

import pytest

from polaris.agent.main import trace_of


@pytest.mark.parametrize("costs,expected", [
    ([None], None),
    ([0.02, None], None),
    ([0.0, None], None),
    ([0.0], 0.0),
    ([0.01, 0.02], 0.03),
    ([], 0.0),
])
def test_main_cost_is_only_a_number_when_every_model_call_is_priced(costs, expected):
    tracer = SimpleNamespace(trace=[{"ms": 100, "cost_usd": cost} for cost in costs], t0=1.0)
    result = trace_of(tracer, SimpleNamespace(calls=[]))
    assert result["totals"]["cost_usd"] == expected
    assert result["totals"]["model_calls"] == len(costs)
