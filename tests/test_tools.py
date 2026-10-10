"""工具层：参数错误给出改法、工具报错、内部异常不崩、截断、故障注入、录制回放、记账。"""
from pydantic import BaseModel
from sqlalchemy import select

from polaris.db.models import ToolCall
from polaris.db.session import session_scope
from polaris.ingest import RawItemIn, ingest_raw_items
from polaris.replay import Cassette
from polaris.tools import REGISTRY
from polaris.tools.base import MAX_OUTPUT_CHARS, Page, ToolSpec
from polaris.tools.runtime import ToolRuntime

SEARCH, GET = REGISTRY["raw_items_search"], REGISTRY["raw_items_get"]


def seed() -> list[int]:
    return ingest_raw_items([
        RawItemIn(source="manual", raw_text="穹宇集团 Agent 开发实习生\n北京，日薪 300-400 元"),
        RawItemIn(source="manual", raw_text="星槎科技 RAG 算法实习生\n上海，投递截止 2026 年 11 月 15 日"),
    ]).ids


def test_search_and_get_happy_path(db):
    ids = seed()
    rt = ToolRuntime()
    out = rt.invoke(SEARCH, {"query": "穹宇集团 Agent"})
    assert f"R-{ids[0]}" in out and "星槎" not in out
    full = rt.invoke(GET, {"item_id": f"R-{ids[1]}"})
    assert "2026 年 11 月 15 日" in full
    with session_scope() as s:
        rows = s.execute(select(ToolCall).order_by(ToolCall.id)).scalars().all()
        assert [(r.tool, r.ok) for r in rows] == [("raw_items_search", True), ("raw_items_get", True)]


def test_invalid_args_tell_the_model_how_to_fix(db):
    rt = ToolRuntime()
    out = rt.invoke(SEARCH, {"query": "", "limit": 99})
    assert out.startswith("出错了") and "limit" in out and "怎么改" in out and "示例" in out
    assert rt.calls[0]["error_kind"] == "invalid_args" and rt.calls[0]["ok"] is False


def test_tool_error_and_empty_result(db):
    seed()
    rt = ToolRuntime()
    assert "没有编号为 R-999999" in rt.invoke(GET, {"item_id": "R-999999"})
    assert rt.calls[-1]["error_kind"] == "tool_error"
    empty = rt.invoke(SEARCH, {"query": "量子计算编译器"})
    assert '"returned": 0' in empty and "hint" in empty


class Args(BaseModel):
    n: int = 1


def test_internal_errors_do_not_crash_the_agent():
    def boom(_: Args) -> str:
        raise RuntimeError("bug")

    rt = ToolRuntime(record_db=False)
    out = rt.invoke(ToolSpec("x_boom", "boom", Args, boom), {})
    assert "工具内部错误" in out and rt.calls[0]["error_kind"] == "internal"


def test_long_output_is_truncated_with_hint():
    rt = ToolRuntime(record_db=False)
    out = rt.invoke(ToolSpec("x_long", "long", Args, lambda a: "字" * (MAX_OUTPUT_CHARS + 100)), {})
    assert len(out) < MAX_OUTPUT_CHARS + 100 and "已截断" in out


def test_page_defaults_and_limits():
    assert Page().limit == 10 and Page().response_format == "concise"


def test_fault_injection_then_recovery(db):
    seed()
    rt = ToolRuntime(faults={"raw_items_search": ["rate_limit"]})
    first = rt.invoke(SEARCH, {"query": "穹宇集团"})
    second = rt.invoke(SEARCH, {"query": "穹宇集团"})
    assert "限流" in first and "穹宇集团" in second
    assert [c["error_kind"] for c in rt.calls] == ["injected", None]


def test_record_then_replay_without_touching_the_database(db, tmp_path):
    ids = seed()
    path = tmp_path / "c.jsonl"
    cassette = Cassette(path, "record")
    live = ToolRuntime(cassette=cassette).invoke(SEARCH, {"query": "星槎科技"})
    cassette.save()
    with session_scope() as s:  # 删掉数据，回放仍然能拿到同样的结果
        s.execute(ToolCall.__table__.delete())
    from polaris.db.models import RawItem
    with session_scope() as s:
        s.execute(RawItem.__table__.delete())
    rt = ToolRuntime(cassette=Cassette(path, "replay"))
    assert rt.invoke(SEARCH, {"query": "星槎科技"}) == live and f"R-{ids[1]}" in live
    assert rt.calls[0]["replayed"] is True
    miss = rt.invoke(SEARCH, {"query": "没录过的查询"})
    assert "录像里没有" in miss and rt.calls[-1]["error_kind"] == "replay_miss"


def test_langchain_tool_routes_invalid_args_through_runtime():
    rt = ToolRuntime(record_db=False)
    tool = rt.as_langchain_tool(SEARCH)
    assert "query" in tool.args
    out = tool.invoke({"limit": 0})
    assert out.startswith("出错了") and rt.calls[0]["error_kind"] == "invalid_args"
