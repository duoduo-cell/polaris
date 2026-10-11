"""Alembic 迁移入口。数据库地址按这个顺序取：命令行或测试传入的 sqlalchemy.url、环境变量 DATABASE_URL、.env。"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from polaris.db.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
SQL_ONLY_INDEXES = {"ix_chunks_bm25"}  # 只在迁移里用 SQL 建的索引（pg_search 的 BM25），自动生成迁移时别把它当成多余的删掉


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    return not (type_ == "index" and name in SQL_ONLY_INDEXES)


def database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("DATABASE_URL")
    if url:
        return url
    from polaris.config import get_settings

    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
