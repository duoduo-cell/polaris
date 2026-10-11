"""测试配置：模型服务一律指向假地址（不会真的调用、不花钱），数据库用单独的 polaris_test 库。"""
import os

# 必须在导入 polaris 之前设好：环境变量优先于 .env
TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://polaris:polaris@127.0.0.1:5432/polaris_test")
os.environ.update({
    "LLM_BASE_URL": "http://llm.test",
    "LLM_API_KEY": "sk-test-chat",
    "LLM_MODEL": "test-model",
    "LLM_JSON_MODE": "auto",
    "LLM_CACHE": "false",
    "LLM_MAX_RETRIES": "2",
    "EMBED_BASE_URL": "http://embed.test/v1",
    "EMBED_API_KEY": "sk-test-embed",
    "EMBED_MODEL": "test-embed",
    "EMBED_DIM": "1024",
    "INGEST_TOKEN": "test-ingest-token",
    "MCP_TOKEN": "test-mcp-token",
    "RERANK_BASE_URL": "",  # 测试不调真的重排服务
    "RERANK_MODEL": "",
    "NEO4J_PASSWORD": "",  # 测试不连开发用的图谱
    "DATABASE_URL": TEST_DB,
})

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from polaris.config import ROOT, get_settings  # noqa: E402
from polaris.db.session import get_engine  # noqa: E402

get_settings.cache_clear()
get_engine.cache_clear()

TABLES = ["memory_events", "memory_facts", "memory_suppressions", "posts", "postings", "jobs", "raw_items", "companies",
          "search_queries", "tool_calls", "llm_calls", "runs"]


def _ensure_test_database() -> None:
    url = make_url(TEST_DB)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(text("select 1 from pg_database where datname = :n"), {"n": url.database}).scalar()
        if not exists:
            conn.execute(text(f'create database "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def engine():
    try:
        _ensure_test_database()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"测试数据库连不上（先 docker compose up -d db）：{e}")
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    return get_engine()


@pytest.fixture
def db(engine):
    """每个用到数据库的测试开始前清空所有表。"""
    with engine.begin() as conn:
        conn.execute(text(f"truncate {', '.join(TABLES)} restart identity cascade"))
    return engine
