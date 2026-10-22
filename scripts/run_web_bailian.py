"""2026-10-22 用户授权的本机网页临时模型入口；只配置此 API 进程，不写 .env。"""
import os
import sys

from polaris.config import get_settings

MODEL = "qwen3.7-plus"


def configure() -> None:
    original = get_settings()
    os.environ.update({
        "LLM_BASE_URL": original.embed_base,
        "LLM_API_KEY": original.embed_key.get_secret_value(),
        "LLM_MODEL": MODEL,
        "LLM_JSON_MODE": "json_object",
        "LLM_ENABLE_THINKING": "false",
        **{f"LLM_MODEL_{role}": MODEL for role in ("CHAT", "EXTRACT", "JUDGE", "BRIEF")},
    })
    get_settings.cache_clear()


if __name__ == "__main__":
    configure()
    from polaris.cli import app

    sys.argv = ["polaris", "serve", *sys.argv[1:]]
    app()
