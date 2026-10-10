"""合成数据：底稿可复现、事实核对规则、仓库里的合成数据文件本身。不调模型。"""
from datetime import date

from polaris.schemas import JobFacts
from polaris.synth.jobs import FIXTURE, Spec, build_specs, check_text, read_fixture


def test_specs_are_deterministic_and_have_planned_mix():
    a, b = build_specs(), build_specs()
    assert [s.__dict__ for s in a] == [s.__dict__ for s in b]
    assert len(a) == 60
    assert sum(s.lang == "en" for s in a) == 4
    assert sum(s.is_negative for s in a) == 6
    assert sum(s.is_cold and not s.dup_of for s in a) == 10
    dups = [s for s in a if s.dup_of]
    assert len(dups) == 10 and all(s.conflict_field for s in dups)


def test_repost_conflicts_with_its_original():
    specs = {s.id: s for s in build_specs()}
    for dup in (s for s in specs.values() if s.dup_of):
        base = specs[dup.dup_of]
        assert getattr(dup, dup.conflict_field) != getattr(base, dup.conflict_field)
        assert (dup.company, dup.title) == (base.company, base.title)


def spec(**kw) -> Spec:
    base = dict(id="T1", company="青梧智能", is_big=False, lang="zh", style="official", title="Agent 开发实习生",
                direction="Agent", focus="", city="北京")
    return Spec(**{**base, **kw})


def test_check_text_accepts_text_with_all_facts():
    s = spec(days_per_week=4, deadline=date(2026, 11, 15))
    text = "青梧智能招聘 Agent 开发实习生，工作地点北京，每周至少4天，投递截止2026年11月15日。"
    assert s.missing() == ["salary", "duration_months", "can_convert", "posted_at", "start"]
    assert check_text(s, text) == []
    assert check_text(s, text.replace(" ", "")) == []  # 空格差异不算错


def test_check_text_catches_missing_and_invented_facts():
    s = spec(days_per_week=4)
    assert any("每周至少4天" in p for p in check_text(s, "青梧智能 Agent 开发实习生 北京"))
    invented = "青梧智能 Agent 开发实习生 北京 每周至少4天，日薪300元，实习3个月"
    problems = check_text(s, invented)
    assert any("薪资" in p for p in problems) and any("实习时长" in p for p in problems)


def test_truth_matches_spec():
    s = spec(salary=(300, 400), can_convert=True, duration_months=6)
    t = s.truth()
    assert (t.salary_min, t.salary_max, t.salary_unit, t.salary_text) == (300, 400, "day", "300-400元/天")
    assert t.can_convert is True and t.duration_months == 6 and t.days_per_week is None


def test_committed_fixture_is_complete_and_checked():
    records = read_fixture(FIXTURE)
    assert len(records) == 60
    assert len({r["id"] for r in records}) == 60
    for r in records:
        JobFacts.model_validate(r["truth"])
        assert r["raw_text"].strip()
        assert r["url"].startswith("https://") and ".example.com/" in r["url"]
    assert all(r["meta"]["checked"] for r in records)
