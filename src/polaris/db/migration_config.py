"""迁移只需数据库配置，目标仍受显式配置文件及演示模式约束。"""
from pydantic_settings import BaseSettings, SettingsConfigDict

from polaris.config import DEFAULT_DATABASE_URL, require_demo_database_url, settings_file


class _DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file_encoding="utf-8-sig", extra="ignore")
    database_url: str = DEFAULT_DATABASE_URL
    demo_mode: bool = False


def migration_database_url(override: str | None = None) -> str:
    settings = _DatabaseSettings(_env_file=settings_file())
    target = override or settings.database_url
    if settings.demo_mode:
        require_demo_database_url(target)
    return target
