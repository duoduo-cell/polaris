"""演示隔离与手写样本的离线检查；不冒充Postgres导入／网页验收。"""
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

from polaris.config import Settings, get_settings
from polaris.data_scope import synthetic_filter
from polaris.db.models import Base, Job, Run
from polaris.demo import VERSION, assert_ready, fixture, require_demo_target, seed
from polaris.search import retrieve
from polaris.tools.base import ToolSpec
from polaris.tools.jobs import JobsSearchArgs
from polaris.tools.runtime import ToolRuntime

URL = "postgresql+psycopg://demo:only-demo@127.0.0.1:55432/polaris_demo"
NOW = datetime(2026, 10, 23, 10, tzinfo=UTC)


@pytest.fixture
def demo_mode(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DATABASE_URL", URL)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def sql(stmt):
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://a:b@127.0.0.1/polaris",
    "postgresql+psycopg://a:b@127.0.0.1/polaris_sandbox",
    "postgresql+psycopg://a:b@remote.example/polaris_demo", "sqlite:///:memory:",
])
def test_demo_rejects_personal_copied_or_remote_database(url):
    with pytest.raises((ValidationError, ValueError), match="DEMO_MODE"):
        Settings(_env_file=None, demo_mode=True, database_url=url)


def test_personal_default_and_demo_scope_are_separate(demo_mode, monkeypatch):
    assert "jobs.is_synthetic IS true" in sql(select(Job).where(synthetic_filter(Job.is_synthetic)))
    assert JobsSearchArgs().big_only is False
    filters = retrieve._filters(retrieve.SearchQuery("Agent", include_synthetic=True))
    assert "chunks.is_synthetic IS true" in " ".join(sql(c) for c in filters)
    monkeypatch.setenv("DEMO_MODE", "false")
    get_settings.cache_clear()
    assert "jobs.is_synthetic IS false" in sql(select(Job).where(synthetic_filter(Job.is_synthetic)))
    assert JobsSearchArgs().big_only is True
    assert "is_synthetic" not in " ".join(sql(c) for c in retrieve._filters(retrieve.SearchQuery("Agent", include_synthetic=True)))


def test_demo_uses_only_keyword_without_embed_graph_or_rerank(demo_mode, monkeypatch):
    observed = []

    @contextmanager
    def scope():
        yield object()

    def forbidden(*args, **kwargs):
        raise AssertionError("演示不得触发向量、图谱、重排或其模型调用")

    monkeypatch.setattr(retrieve, "session_scope", scope)
    monkeypatch.setattr(retrieve, "keyword", lambda s, q, text, *a: observed.append(q) or [])
    monkeypatch.setattr(retrieve, "vector", forbidden)
    monkeypatch.setattr(retrieve, "graph", forbidden)
    monkeypatch.setattr(retrieve, "_rerank", forbidden)
    monkeypatch.setattr(retrieve, "_load", lambda *a: {})
    monkeypatch.setattr(retrieve, "_tokens", lambda *a: {})
    hits, trace = retrieve.search(retrieve.SearchQuery("Agent"), llm=object())
    assert not hits and observed and observed[0].channels == ("keyword",)
    assert observed[0].rerank is False
    assert "未运行向量、图谱或重排" in retrieve.degraded(trace)


@pytest.mark.parametrize("name,live", [("source_open", True), ("platform_search", True), ("kg_stats", False), ("future_live", True)])
def test_demo_runtime_blocks_real_tools_even_if_called_directly(demo_mode, name, live):
    invoked = []
    spec = ToolSpec(name, "合成边界探针", JobsSearchArgs, lambda args: invoked.append(args), live=live)
    runtime = ToolRuntime(record_db=False)
    output = runtime.invoke(spec, {})
    assert not invoked and "合成演示" in output
    assert runtime.calls[-1]["error_kind"] == "demo_boundary"


def test_fixture_contains_only_synthetic_sources_with_distinct_date_meanings():
    rows = fixture(NOW)
    assert rows.counts == {"raw_items": 6, "jobs": 5, "postings": 5, "posts": 1,
                           "interview_questions": 1, "chunks": 12, "memory_facts": 8}
    raws = rows.groups[0]
    for raw in raws:
        assert raw.is_synthetic is True and raw.source == "demo"
        assert raw.url.startswith("https://example.invalid/polaris-demo/")
        assert raw.published_at < raw.fetched_at < raw.last_seen_at
        assert "人工虚构" in raw.raw_text
    assert all(chunk.is_synthetic is True and chunk.embedding is None for chunk in rows.groups[5])
    assert rows.groups[4][0].round is None and "一面与二面" in rows.groups[0][-1].raw_text
    assert all("人工虚构" in f.source_text for f in rows.groups[-1])


def test_seeded_question_post_parser_and_tool_projection_agree_on_source_facts():
    from polaris.interviews import profile
    from polaris.tools.jobs import _post_row
    from polaris.tools.knowledge import question_row

    rows = fixture(NOW).groups
    post, raw, seeded = rows[3][0], rows[0][-1], rows[4][0]
    parsed = profile(post, raw.raw_text, raw.raw_json)["questions"][0]
    for field in ("company", "role", "team", "job_type", "round", "category", "material", "attribution", "evidence"):
        assert getattr(seeded, field) == parsed[field], field
    post_card, question_card = _post_row(post, raw, True), question_row(seeded, post, raw)
    assert post_card["interview_company"] == question_card["company"]
    assert question_card["company"] in post_card["companies"]
    assert question_card["round"] is None and "一面与二面" in question_card["post_title"]


def test_fixture_exercises_real_matching_without_inventing_missing_personal_months():
    from polaris.match import job_match
    from polaris.proactive.decide import Profile

    jobs = fixture(NOW).groups[1]
    profile = Profile(cities=["上海"], roles=["Agent"], days_per_week=4, job_type="intern")
    background = {"degree": "本科在读", "paper_experience": "无论文"}
    fits = [job_match(j, profile, background=background) for j in jobs]
    blocked = {c["key"]: c["ok"] for c in fits[2]["checks"]}
    assert blocked["degree"] is False and blocked["paper"] is False
    assert fits[2]["eligibility"] == "incompatible"
    assert next(x for x in fits[0]["requirements_review"] if x["key"] == "months")["status"] == "unknown"
    assert next(c for c in fits[3]["checks"] if c["key"] == "days")["ok"] is None


def test_seed_validates_injected_engine_before_entering_any_transaction(demo_mode, monkeypatch):
    from polaris import demo

    monkeypatch.setattr(demo, "session_scope", lambda *a: pytest.fail("不应进入事务"))
    with pytest.raises(ValueError, match="polaris_demo"):
        seed(SimpleNamespace(url=make_url(URL.replace("polaris_demo", "polaris"))), demo_mode, NOW)
    with pytest.raises(ValueError, match="DEMO_MODE"):
        require_demo_target(demo_mode.model_copy(update={"demo_mode": False}), SimpleNamespace(url=make_url(URL)))


class SessionProbe:
    def __init__(self, values):
        self.values, self.added, self.executed = iter(values), [], []

    def scalar(self, stmt):
        self.executed.append(sql(stmt))
        return next(self.values)

    def execute(self, stmt):
        self.executed.append(str(stmt))

    def add_all(self, rows):
        self.added.extend(rows)

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


def test_nonempty_demo_target_is_never_cleared_or_overwritten(demo_mode, monkeypatch):
    from polaris import demo

    probe = SessionProbe([None, 1])

    @contextmanager
    def scope(engine):
        yield probe

    monkeypatch.setattr(demo, "session_scope", scope)
    with pytest.raises(ValueError, match="并非空库"):
        seed(SimpleNamespace(url=make_url(URL)), demo_mode, NOW)
    assert not probe.added
    assert not any(x in " ".join(probe.executed).lower() for x in ("truncate", "delete", "drop"))


def test_business_empty_but_model_history_present_is_not_an_empty_demo_target(demo_mode, monkeypatch):
    from polaris import demo

    probe = SessionProbe([None] + [0] * len(Base.metadata.sorted_tables) + ["checkpoints", 1])

    @contextmanager
    def scope(engine):
        yield probe

    monkeypatch.setattr(demo, "session_scope", scope)
    with pytest.raises(ValueError, match="存在检查点记录"):
        seed(SimpleNamespace(url=make_url(URL)), demo_mode, NOW)
    assert not probe.added


@pytest.mark.parametrize("values", [[None], [Run(params={"fixture": VERSION}), 1]])
def test_missing_seed_marker_or_real_content_refuses_startup(values):
    with pytest.raises(ValueError, match="拒绝启动"):
        assert_ready(SessionProbe(values))


def test_repeat_seed_keeps_user_history_and_does_not_insert_again(demo_mode, monkeypatch):
    from polaris import demo

    marker = Run(params={"fixture": VERSION})
    probe = SessionProbe([marker, marker, 0, 0, 0, 0, 0])

    @contextmanager
    def scope(engine):
        yield probe

    monkeypatch.setattr(demo, "session_scope", scope)
    result = seed(SimpleNamespace(url=make_url(URL)), demo_mode, NOW)
    assert result["status"] == "already_seeded" and not probe.added


def test_explicit_env_file_does_not_fall_back_to_personal_owner_token(tmp_path, monkeypatch):
    path = tmp_path / "demo.env"
    path.write_text("OWNER_TOKEN=synthetic-owner-token\n", encoding="utf-8")
    monkeypatch.delenv("OWNER_TOKEN", raising=False)
    monkeypatch.setenv("POLARIS_ENV_FILE", str(path))
    get_settings.cache_clear()
    try:
        assert get_settings().owner_token.get_secret_value() == "synthetic-owner-token"
    finally:
        get_settings.cache_clear()


def test_missing_explicit_config_file_never_falls_back_to_personal_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("POLARIS_ENV_FILE", str(tmp_path / "does-not-exist.env"))
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="拒绝回退"):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_demo_ingest_and_real_brief_are_unavailable_without_reading_files(demo_mode):
    from fastapi import HTTPException

    from polaris.api.app import require_ingest_token
    from polaris.api.personal import brief_endpoint

    with pytest.raises(HTTPException) as error:
        require_ingest_token("Bearer test-ingest-token")
    assert error.value.status_code == 503
    assert brief_endpoint()["markdown"] is None


def test_demo_agenda_reads_only_the_demo_database(db, monkeypatch, tmp_path):
    """10-24：演示的“今天”页原来整页 503；现在只汇总演示库里的事，不碰本机采集记录、简报和推送设置文件。"""
    from polaris import agenda as agenda_module
    from polaris.proactive import daily

    monkeypatch.setattr(daily, "settings", lambda: pytest.fail("演示不读个人推送设置"))
    (tmp_path / f"{datetime.now(UTC).astimezone(agenda_module.BEIJING).date().isoformat()}.md").write_text("真实简报")
    monkeypatch.setattr(daily, "BRIEFS", tmp_path)
    out = agenda_module.agenda(demo=True)
    assert out["doing"][0]["text"].startswith("合成演示：不运行采集")
    assert out["last_collect"] is None
    assert not [x for x in out["scheduled"] + out["done"] if x["kind"] in ("brief", "collect", "jobs")]


def test_default_demo_command_does_not_even_load_settings_or_access_database(monkeypatch, capsys):
    import json

    from polaris import demo

    monkeypatch.setattr("sys.argv", ["polaris.demo"])
    monkeypatch.setattr(demo, "get_settings", lambda: pytest.fail("默认检查不读取私人配置"))
    monkeypatch.setattr(demo, "get_engine", lambda: pytest.fail("默认检查不连接数据库"))
    demo.main()
    output = json.loads(capsys.readouterr().out)
    assert output["database_accessed"] is False and output["counts"]["jobs"] == 5


def test_seed_requires_explicit_apply_before_loading_settings(monkeypatch, capsys):
    from polaris import demo

    monkeypatch.setattr("sys.argv", ["polaris.demo", "--seed"])
    monkeypatch.setattr(demo, "get_settings", lambda: pytest.fail("缺少apply不能加载目标并写入"))
    demo.main()
    assert "offline_check" in capsys.readouterr().out
