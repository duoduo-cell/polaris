"""主动决策（M6）：纯规则，不连数据库、不调模型。"""
from datetime import UTC, date, datetime, timedelta

from polaris.proactive.decide import Item, Profile, Sent, decide

NOW = datetime(2026, 10, 11, 10, tzinfo=UTC)
P = Profile(roles=["Agent"], cities=["北京"], job_type="实习", days_per_week=4, companies_like=["字节"],
            companies_avoid=["拼多多"], constraints=["不接受外包"], interviewing=["美团"])


def job(key: str, **kw) -> Item:
    base = dict(kind="job", title="Agent 开发实习生", company="字节跳动", cities=["北京"], job_type="intern",
                directions=["Agent"], first_seen=NOW - timedelta(hours=2))
    return Item(key=key, **{**base, **kw})


def channels(items: list[Item], history=(), now=NOW, level="normal") -> dict[str, str]:
    return {d.key: d.channel for d in decide(items, P, list(history), now, level)}


def test_hard_exclusions_are_dropped_with_reasons():
    ds = {d.key: d for d in decide([job("a", company="拼多多"), job("b", text="外包驻场"), job("c", job_type="social"),
                                    job("d", cities=["杭州"]), job("e", directions=["前端"], title="前端实习")], P, [], NOW)}
    assert all(d.channel == "drop" for d in ds.values())
    assert "不想去拼多多" in ds["a"].reasons[0] and "外包" in ds["b"].reasons[0] and "社招" in ds["c"].reasons[0]
    assert "杭州" in ds["d"].reasons[0] and "方向" in ds["e"].reasons[0]


def test_good_new_match_is_pushed_but_not_twice_or_at_night():
    assert channels([job("a")]) == {"a": "push"}
    sent = Sent("a", "字节跳动", ["Agent"], NOW - timedelta(days=1))
    assert channels([job("a")], [sent]) == {"a": "drop"}  # 推过了
    assert channels([job("a")], now=datetime(2026, 10, 11, 23, 30, tzinfo=UTC)) == {"a": "brief"}  # 夜里
    assert channels([job("a")], level="off") == {"a": "brief"}


def test_budget_goes_to_the_most_urgent():
    urgent = job("u", deadline=date(2026, 10, 12))
    out = channels([job("plain"), urgent], level="low")  # 每天 1 条
    assert out == {"u": "push", "plain": "brief"}


def test_requirement_conflicts_and_prep_material_only_reach_the_brief():
    out = channels([job("days", days_per_week=5),
                    Item("p1", "post", "字节面经", company="字节跳动", directions=["Agent"], post_type="面经", first_seen=NOW),
                    Item("p2", "post", "美团面经", company="美团", directions=["Agent"], post_type="面经", first_seen=NOW)])
    assert out == {"days": "brief", "p1": "brief", "p2": "push"}  # 正在面美团，美团的面经要推


def test_repeated_dismissals_tone_it_down():
    hist = [Sent(f"k{i}", "快手", ["算法"], NOW - timedelta(days=3 + i), feedback="dismissed") for i in range(2)]
    assert channels([job("ks", company="快手")], hist) == {"ks": "brief"}


def test_role_needs_concept_and_compatible_function():
    from polaris.proactive.decide import role_hit

    assert role_hit("Agent 开发", job("a", title="AI Agent开发实习生-TikTok搜索")) == 1.0
    assert role_hit("Agent 开发", job("b", title="Agent 产品实习生")) == 0.0  # 职能不对
    assert role_hit("Agent 开发", job("c", title="GUI Agent算法实习生 - 开发者服务")) == 0.0  # 部门名里的"开发"不算
    assert role_hit("Agent 开发", job("d", title="大数据开发实习生", directions=["Agent"])) == 0.6  # 只在标签里


def test_non_big_company_is_dropped_when_big_only():
    from dataclasses import replace

    out = {d.key: d for d in decide([job("x", company="某小公司")], replace(P, big_only=True), [], NOW)}
    assert out["x"].channel == "drop" and "大厂名单" in out["x"].reasons[0]
