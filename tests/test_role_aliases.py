"""实际方向误判回归：Engineering与Engineer同义，不能串到算法/产品职能。"""
import pytest

from polaris.proactive.decide import Item, role_hit


def item(title, *, directions=None):
    return Item("alias-case", "job", title, directions=directions or ["Agent"])


@pytest.mark.parametrize("role", ["Agent Engineering", "Agent Engineer", "AGENT ENGINEERING", "Agent 开发"])
@pytest.mark.parametrize("title", ["Agent开发实习生", "Agent研发实习生", "Agent Engineering Intern"])
def test_equivalent_engineering_roles_match_same_job(role, title):
    assert role_hit(role, item(title)) == 1.0


@pytest.mark.parametrize("title", ["Agent产品实习生", "GUI Agent算法实习生 - 开发者服务", "Agent研究实习生"])
def test_engineering_does_not_match_other_functions(title):
    assert role_hit("Agent Engineering", item(title)) == 0.0


def test_label_only_stays_weak_and_unrelated_concepts_stay_unmatched():
    assert role_hit("Agent Engineering", item("大数据开发实习生")) == 0.6
    assert role_hit("Agent Engineering", item("后端开发实习生", directions=["后端"])) == 0.0
