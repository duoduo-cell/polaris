"""每天采集量的规则：顺利就加、有警告减半、被拦冷却一天。"""
from datetime import datetime, timedelta

from polaris.collectors import budget
from polaris.db.models import Run
from polaris.db.session import session_scope
from polaris.report import BEIJING

NOW = datetime(2026, 10, 12, 10, 0, tzinfo=BEIJING)


def add_run(when: datetime, calls: int, cap: int | None = 300, failures: int = 0, stopped: str | None = None):
    with session_scope() as s:
        s.add(Run(kind="collect", params={"source": "xhs", "cap": cap}, status="ok", started_at=when,
                  stats={"calls": calls, "failures": failures, "stopped": stopped}))


def test_first_day_starts_low(db):
    assert budget.today_cap("xhs", NOW)[0] == budget.policy()["xhs"]["start"]


def test_grows_after_clean_full_day(db):
    add_run(NOW - timedelta(days=1), calls=290)
    assert budget.today_cap("xhs", NOW)[0] == 390


def test_keeps_when_not_used_up(db):
    add_run(NOW - timedelta(days=1), calls=100)
    assert budget.today_cap("xhs", NOW)[0] == 300  # 前一天上限 300 没用满，保持


def test_halves_on_soft_warning(db):
    add_run(NOW - timedelta(days=1), calls=200, failures=60)
    cap, why = budget.today_cap("xhs", NOW)
    assert cap == 150 and "警告" in why


def test_cools_down_after_block(db):
    add_run(NOW - timedelta(hours=20), calls=120, stopped="疑似被拦：页面出现「安全验证」")
    assert budget.today_cap("xhs", NOW)[0] == 0  # 不满 24 小时
    assert budget.today_cap("xhs", NOW + timedelta(hours=5))[0] == 150  # 满 24 小时后减半


def test_blocked_today_means_zero(db):
    add_run(NOW - timedelta(hours=1), calls=50, stopped="连续失败 3 次：疑似被拦")
    assert budget.today_cap("xhs", NOW)[0] == 0
    assert budget.used_today("xhs", NOW) == 50


def test_login_expiry_is_not_a_block(db):
    add_run(NOW - timedelta(hours=1), calls=1, stopped="not_authenticated")
    assert budget.today_cap("xhs", NOW)[0] > 0  # 令牌过期不是被拦，不清零


def test_hourly_limit(db):
    add_run(NOW - timedelta(minutes=30), calls=budget.policy()["xhs"]["per_hour"])
    assert budget.hour_left("xhs", NOW) == 0
    assert budget.hour_left("xhs", NOW + timedelta(minutes=40)) == budget.policy()["xhs"]["per_hour"]
