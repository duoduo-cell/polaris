"""背景投影保留原有证据，不将项目名称扩成具体实现或把推测升级为自述。"""
from datetime import UTC, datetime
from types import SimpleNamespace

from polaris.agent.main import _background_context, _system_prompt
from polaris.db.session import session_scope
from polaris.memory.update import Candidate, Context, apply


def project(statement, *, kind="stated"):
    return SimpleNamespace(attribute="project", value="GraphRAG+情报分析", statement=statement, kind=kind,
                           qualifier=None, valid_from=None, id=1)


def test_project_responsibility_is_not_lost_when_value_only_names_project():
    out = _background_context([project("用户做过GraphRAG+情报分析，并担任项目负责人")])
    assert "项目负责人" in out and "用户自述" in out
    assert "没有说明的架构" in out and "均未知" in out


def test_explicit_positive_and_negative_details_remain_explicit():
    for statement in ["用户用三个Agent负责检索、分析和校验", "用户项目是单链路，没有实现Multi-Agent协作"]:
        out = _background_context([project(statement)])
        assert f"用户自述：{statement}" in out


def test_inferred_statement_is_not_promoted_to_user_stated_fact():
    out = _background_context([project("用户可能熟悉多Agent编排", kind="inferred")])
    assert "推测，待核实：用户可能熟悉多Agent编排" in out
    assert "用户自述：用户可能" not in out


def test_main_prompt_uses_saved_statement_but_does_not_change_memory(db):
    now = datetime(2026, 10, 12, tzinfo=UTC)
    with session_scope() as s:
        apply(Context(s, now, "我负责GraphRAG+情报分析项目"),
              Candidate("project", "GraphRAG+情报分析", "用户负责GraphRAG+情报分析项目"))
    prompt = _system_prompt(now, [])
    assert "用户自述：用户负责GraphRAG+情报分析项目" in prompt
    assert "项目名称不能证明用户用了哪种架构" in prompt
    assert "只有用户明确自述做过或没做过" in prompt


def test_empty_background_stays_empty():
    assert _background_context([]) == "（还没有记住关于你的事）"
