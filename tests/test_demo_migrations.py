"""执行实际Alembic入口的配置边界与离线SQL；引擎构造被阻止，不连接数据库。"""
import io

import pytest
import sqlalchemy
from alembic import command, context
from alembic.config import Config
from alembic.script import ScriptDirectory

from polaris.config import ROOT, get_settings

DEMO_URL = "postgresql+psycopg://demo:synthetic-only@127.0.0.1:55432/polaris_demo"
PERSONAL_URL = "postgresql+psycopg://demo:synthetic-only@127.0.0.1:5432/polaris"


@pytest.fixture
def migration(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    path = tmp_path / "selected.env"
    path.write_text(f"DEMO_MODE=true\nDATABASE_URL={DEMO_URL}\n", encoding="utf-8")
    monkeypatch.setenv("POLARIS_ENV_FILE", str(path))
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *a, **kw: pytest.fail("迁移不应构造引擎"))
    output = io.StringIO()
    cfg = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    cfg.attributes["configure_logger"] = False
    get_settings.cache_clear()
    yield cfg, output, path
    get_settings.cache_clear()


@pytest.mark.parametrize("sql", [True, False])
@pytest.mark.parametrize("source", ["environment", "alembic_override"])
def test_demo_migration_rejects_personal_target_before_engine_or_sql(migration, monkeypatch, sql, source):
    cfg, output, _ = migration
    if source == "environment":
        monkeypatch.setenv("DATABASE_URL", PERSONAL_URL)
    else:
        cfg.set_main_option("sqlalchemy.url", PERSONAL_URL)
    with pytest.raises(ValueError, match="polaris_demo") as caught:
        command.upgrade(cfg, "head", sql=sql)
    assert "synthetic-only" not in str(caught.value) and output.getvalue() == ""


@pytest.mark.parametrize("missing", ["absent", "blank"])
def test_selected_missing_file_is_not_bypassed_by_explicit_migration_url(migration, monkeypatch, missing):
    cfg, output, path = migration
    monkeypatch.setenv("DATABASE_URL", DEMO_URL)
    monkeypatch.setenv("POLARIS_ENV_FILE", "" if missing == "blank" else str(path.with_name("missing.env")))
    with pytest.raises(ValueError, match="拒绝回退"):
        command.upgrade(cfg, "head", sql=True)
    assert output.getvalue() == ""


def test_all_demo_migrations_emit_offline_without_model_configuration(migration, monkeypatch):
    cfg, output, _ = migration
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    command.upgrade(cfg, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "CREATE EXTENSION IF NOT EXISTS pg_search" in sql
    assert "CREATE INDEX ix_chunks_bm25" in sql and "content::pdb.jieba" in sql
    assert "CREATE TABLE chat_tasks" in sql and "CREATE TABLE job_followup_events" in sql
    script = ScriptDirectory.from_config(cfg)
    revisions = list(script.walk_revisions())
    assert len(revisions) == 14 and all(revision.revision in sql for revision in revisions)
    assert script.get_current_head() in sql and sql.strip().endswith("COMMIT;")


def test_personal_mode_keeps_explicit_url_precedence_without_model_keys(migration, monkeypatch):
    cfg, _, path = migration
    path.write_text("DEMO_MODE=false\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", DEMO_URL)
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    cfg.set_main_option("sqlalchemy.url", PERSONAL_URL)
    original, urls = context.configure, []

    def configure(*a, **kw):
        urls.append(kw.get("url"))
        return original(*a, **kw)

    monkeypatch.setattr(context, "configure", configure)
    command.upgrade(cfg, "head", sql=True)
    assert urls == [PERSONAL_URL]


def test_windows_utf8_bom_does_not_hide_demo_mode(migration, monkeypatch):
    cfg, _, path = migration
    path.write_text(f"DEMO_MODE=true\nDATABASE_URL={DEMO_URL}\n", encoding="utf-8-sig")
    monkeypatch.setenv("DATABASE_URL", PERSONAL_URL)
    with pytest.raises(ValueError, match="polaris_demo"):
        command.upgrade(cfg, "head", sql=True)


def test_application_reads_the_same_windows_bom_demo_guard(migration, monkeypatch):
    _, _, path = migration
    path.write_text(f"DEMO_MODE=true\nDATABASE_URL={DEMO_URL}\n", encoding="utf-8-sig")
    monkeypatch.setenv("DATABASE_URL", PERSONAL_URL)
    with pytest.raises(ValueError, match="polaris_demo"):
        get_settings()
