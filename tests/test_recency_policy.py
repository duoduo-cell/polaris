"""调度边界：不能未经用户要求把发布时间换成更新时间，拒绝也必须留记录。"""
import json
from dataclasses import replace

import pytest

from polaris.tools import REGISTRY
from polaris.tools.runtime import ToolRuntime


@pytest.mark.parametrize("user_text,allowed", [
    ("找最新三天的大厂Agent实习", False),
    ("只按发布日期，不要用更新时间", False),
    ("可以查最近更新的岗位", True),
    ("我想看更新时间在最近三天的岗位", True),
])
def test_date_basis_is_authorized_by_user_request(user_text, allowed):
    executed = []
    spec = replace(REGISTRY["jobs_search"], func=lambda args: executed.append(args) or {"returned": 0, "items": []})
    rt = ToolRuntime(agent="main", record_db=False)
    rt.user_request = user_text
    out = rt.invoke(spec, {"since_days": 3, "date_basis": "reported"})
    assert bool(executed) == allowed
    assert rt.calls[0]["ok"] == allowed
    assert rt.calls[0]["error_kind"] == (None if allowed else "constraint_violation")
    if allowed:
        assert json.loads(out)["returned"] == 0
    else:
        assert "用户没有要求改查更新时间" in out


def test_rejected_date_change_can_recover_using_publication_without_losing_failure():
    spec = replace(REGISTRY["jobs_search"], func=lambda args: {"returned": 0, "items": []})
    rt = ToolRuntime(agent="main", record_db=False)
    rt.user_request = "最近三天发布的实习"
    rt.invoke(spec, {"since_days": 3, "date_basis": "reported"})
    assert json.loads(rt.invoke(spec, {"since_days": 3, "date_basis": "published"}))["returned"] == 0
    assert [c["ok"] for c in rt.calls] == [False, True]


@pytest.mark.parametrize("user_text,days,allowed", [
    ("最新三天的大厂Agent实习", 7, False),
    ("最近3天的实习", None, False),
    ("最近3天的实习", 3, True),
    ("最近三天，没有的话可以放宽到一周", 7, True),
    ("对比最近3天和最近7天的岗位", 7, True),
])
def test_explicit_window_is_not_silently_widened(user_text, days, allowed):
    executed = []
    spec = replace(REGISTRY["jobs_search"], func=lambda args: executed.append(args) or {"returned": 0, "items": []})
    rt = ToolRuntime(agent="main", record_db=False)
    rt.user_request = user_text
    rt.invoke(spec, {"since_days": days})
    assert bool(executed) == allowed
    assert rt.calls[0]["error_kind"] == (None if allowed else "constraint_violation")
