"""配置：全部从环境变量和项目根目录的 .env 读，key 用 SecretStr，不会出现在日志和 repr 里。"""
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

Role = Literal["chat", "extract", "judge", "brief"]

# 表结构里向量列的维度。改它需要一次数据库迁移，所以写成常量，配置只做核对。
EMBED_DIM = 1024


def openai_base(url: str) -> str:
    """OpenAI SDK 要的 base_url 以 /v1 结尾；.env 里带不带 /v1 都接受。"""
    url = url.strip().rstrip("/")
    return url if url.endswith("/v1") else url + "/v1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # 对话模型。四个角色不单独配时都用 llm_model；提交时按 PLAN 第 7 节分给不同的 Nemotron
    llm_base_url: str
    llm_api_key: SecretStr
    llm_model: str
    llm_model_chat: str | None = None
    llm_model_extract: str | None = None
    llm_model_judge: str | None = None
    llm_model_brief: str | None = None
    llm_timeout: float = 120.0
    llm_max_retries: int = 4
    llm_concurrency: int = 4
    llm_cache: bool = False
    # 结构化输出：auto 先试 json_schema，不支持再换 json_object；已知不支持时直接写 json_object 省一次调用
    llm_json_mode: Literal["auto", "json_object"] = "auto"

    # 向量服务可以和对话服务分开；不配就用对话服务
    embed_base_url: str | None = None
    embed_api_key: SecretStr | None = None
    embed_model: str = "text-embedding-v4"
    embed_dim: int = EMBED_DIM
    embed_batch: int = 10

    database_url: str = "postgresql+psycopg://polaris:polaris@127.0.0.1:5432/polaris"
    ingest_token: SecretStr | None = None

    def model_for(self, role: Role) -> str:
        return getattr(self, f"llm_model_{role}") or self.llm_model

    @property
    def chat_base(self) -> str:
        return openai_base(self.llm_base_url)

    @property
    def embed_base(self) -> str:
        return openai_base(self.embed_base_url) if self.embed_base_url else self.chat_base

    @property
    def embed_key(self) -> SecretStr:
        return self.embed_api_key if self.embed_base_url and self.embed_api_key else self.llm_api_key

    @staticmethod
    def host(url: str) -> str:
        return urlparse(url).netloc

    def public_summary(self) -> dict:
        """给 /health 用：只有模型名和服务域名，没有 key。"""
        return {
            "chat_host": self.host(self.chat_base),
            "models": {role: self.model_for(role) for role in ("chat", "extract", "judge", "brief")},
            "embed_host": self.host(self.embed_base),
            "embed_model": self.embed_model,
            "embed_dim": self.embed_dim,
            "cache": self.llm_cache,
        }


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.embed_dim != EMBED_DIM:
        raise ValueError(f"EMBED_DIM={s.embed_dim}，但表结构的向量列是 {EMBED_DIM} 维；改维度要先做数据库迁移")
    return s
