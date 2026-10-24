"""配置：全部从环境变量和项目根目录的 .env 读，key 用 SecretStr，不会出现在日志和 repr 里。"""
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_URL = "postgresql+psycopg://polaris:polaris@127.0.0.1:5432/polaris"

Role = Literal["chat", "extract", "judge", "brief"]

# 表结构里向量列的维度。改它需要一次数据库迁移，所以写成常量，配置只做核对。
EMBED_DIM = 1024


def openai_base(url: str) -> str:
    """OpenAI SDK 要的 base_url 以 /v1 结尾；.env 里带不带 /v1 都接受。"""
    url = url.strip().rstrip("/")
    return url if url.endswith("/v1") else url + "/v1"


def settings_file() -> Path:
    config_file = os.environ.get("POLARIS_ENV_FILE")
    if config_file is not None and (not config_file.strip() or not Path(config_file).is_file()):
        raise ValueError("POLARIS_ENV_FILE指定的文件不存在，拒绝回退到个人配置或默认数据库")
    return Path(config_file) if config_file is not None else ROOT / ".env"


def require_demo_database_url(value: str | URL) -> None:
    error = "DEMO_MODE仅允许本机或独立Compose内的polaris_demo数据库"
    try:
        url = make_url(value)
    except Exception:
        raise ValueError(error) from None
    if (url.get_backend_name() != "postgresql" or url.database != "polaris_demo"
            or url.host not in ("127.0.0.1", "localhost", "::1", "db")):
        raise ValueError(error)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8-sig", extra="ignore")

    # 对话模型。四个角色不单独配时都用 llm_model；提交时按 PLAN 第 7 节分给不同的 Nemotron
    llm_base_url: str
    llm_api_key: SecretStr
    llm_model: str
    llm_model_chat: str | None = None
    llm_model_extract: str | None = None
    llm_model_judge: str | None = None
    llm_model_brief: str | None = None
    llm_timeout: float = 120.0
    # 建连（含 TLS 握手）单独限时：10-24 本机加速器隧道下握手 1~8 秒、时常卡死，原来要等 30 秒以上才报错重试
    llm_connect_timeout: float = 10.0
    # 这些域名在本机加速器（fake-ip）下绕开隧道直连，见 llm/direct.py；空字符串关闭
    llm_direct_hosts: str = "dashscope.aliyuncs.com"
    llm_max_retries: int = 4
    llm_concurrency: int = 4
    llm_cache: bool = False
    # 结构化输出：auto 先试 json_schema，不支持再换 json_object；已知不支持时直接写 json_object 省一次调用
    llm_json_mode: Literal["auto", "json_object"] = "auto"
    llm_enable_thinking: bool | None = None  # 仅支持该参数的服务显式配置；未配置不发送
    llm_chat_enable_thinking: bool | None = None  # 仅主对话覆盖，不影响抽取/审查/简报
    llm_chat_thinking_budget: int | None = Field(default=None, ge=1, le=32768)

    # 向量服务可以和对话服务分开；不配就用对话服务
    embed_base_url: str | None = None
    embed_api_key: SecretStr | None = None
    embed_model: str = "text-embedding-v4"
    embed_dim: int = EMBED_DIM
    embed_batch: int = 10
    # 重排服务（M4）：不配就跳过重排，直接用融合分。key 和向量服务共用（开发期都是阿里云百炼）
    # 读图（小红书图文笔记里的简历、offer、长面经截图）：走和向量同一个服务的地址和 key；模型名可在 .env 里用 VISION_MODEL 覆盖
    vision_model: str = "qwen3.5-ocr"  # 10-12 查过这个账号的百炼上可用：qwen3.5-ocr、qwen-vl-ocr、qwen3-vl-plus
    rerank_base_url: str | None = None
    rerank_model: str | None = None

    database_url: str = DEFAULT_DATABASE_URL
    demo_mode: bool = False
    ingest_token: SecretStr | None = None
    mcp_token: SecretStr | None = None  # /mcp 的口令；不配就关闭 MCP
    owner_token: SecretStr | None = None  # 你自己用的接口（/api/me：对话、记忆、简报）的口令；不配就关闭
    neo4j_uri: str = "bolt://127.0.0.1:7687"  # 知识图谱（M4）
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr | None = None

    @model_validator(mode="after")
    def validate_demo_database(self) -> "Settings":
        if self.demo_mode:
            require_demo_database_url(self.database_url)
        return self

    def request_timeout(self) -> Any:
        import openai

        from polaris.llm.direct import install

        install(self.llm_direct_hosts)  # 每个模型客户端都经过这里取超时，顺带启用直连
        return openai.Timeout(self.llm_timeout, connect=self.llm_connect_timeout)

    def model_for(self, role: Role) -> str:
        return getattr(self, f"llm_model_{role}") or self.llm_model

    def thinking_options(self, role: Role, explicit: dict[str, Any] | None = None) -> dict[str, Any]:
        explicit = explicit or {}
        enabled = self.llm_enable_thinking
        if role == "chat" and self.llm_chat_enable_thinking is not None:
            enabled = self.llm_chat_enable_thinking
        options = {"enable_thinking": enabled} if enabled is not None else {}
        if role == "chat" and enabled and self.llm_chat_thinking_budget is not None:
            options["thinking_budget"] = self.llm_chat_thinking_budget
        options.update(explicit)
        if options.get("enable_thinking") is False and "thinking_budget" not in explicit:
            options.pop("thinking_budget", None)
        return options

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

    @property
    def rerank_enabled(self) -> bool:
        return bool(self.rerank_base_url and self.rerank_model)

    def public_summary(self) -> dict:
        """给 /health 用：只有模型名和服务域名，没有 key。"""
        return {
            "chat_host": self.host(self.chat_base),
            "models": {role: self.model_for(role) for role in ("chat", "extract", "judge", "brief")},
            "embed_host": self.host(self.embed_base),
            "embed_model": self.embed_model,
            "embed_dim": self.embed_dim,
            "rerank_host": self.host(self.rerank_base_url) if self.rerank_base_url else None,
            "rerank_model": self.rerank_model,
            "cache": self.llm_cache,
            "dataset": "synthetic_demo" if self.demo_mode else "personal",
        }


@lru_cache
def get_settings() -> Settings:
    s = Settings(_env_file=settings_file())
    if s.embed_dim != EMBED_DIM:
        raise ValueError(f"EMBED_DIM={s.embed_dim}，但表结构的向量列是 {EMBED_DIM} 维；改维度要先做数据库迁移")
    return s
