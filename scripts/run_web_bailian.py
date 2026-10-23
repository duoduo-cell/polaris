"""2026-10-22 用户授权的本机网页临时模型入口；只配置此 API 进程，不写 .env。"""
import argparse
import os
import sys

from polaris.config import get_settings

MODEL = "qwen3.7-plus"


def configure(*, chat_thinking_budget: int | None = None) -> None:
    original = get_settings()
    os.environ.update({
        "LLM_BASE_URL": original.embed_base,
        "LLM_API_KEY": original.embed_key.get_secret_value(),
        "LLM_MODEL": MODEL,
        "LLM_JSON_MODE": "json_object",
        "LLM_ENABLE_THINKING": "false",
        "LLM_CHAT_ENABLE_THINKING": str(chat_thinking_budget is not None).lower(),
        **{f"LLM_MODEL_{role}": MODEL for role in ("CHAT", "EXTRACT", "JUDGE", "BRIEF")},
    })
    if chat_thinking_budget is not None:
        os.environ["LLM_CHAT_THINKING_BUDGET"] = str(chat_thinking_budget)
    else:
        os.environ.pop("LLM_CHAT_THINKING_BUDGET", None)
    get_settings.cache_clear()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--chat-thinking-budget", type=int)
    options, rest = parser.parse_known_args()
    configure(chat_thinking_budget=options.chat_thinking_budget)
    from polaris.cli import app

    sys.argv = ["polaris", "serve", *rest]
    app()
