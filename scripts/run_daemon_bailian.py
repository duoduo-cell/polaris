"""用户于 2026-10-22 确认的临时采集模型；只覆盖当前子进程，不改 .env。"""
import os
import sys

from polaris.config import ROOT, get_settings


def configure() -> None:
    original = get_settings()
    os.environ.update({
        "LLM_BASE_URL": original.embed_base,
        "LLM_API_KEY": original.embed_key.get_secret_value(),
        "LLM_MODEL": "qwen-plus",
        "LLM_JSON_MODE": "json_object",
        **{f"LLM_MODEL_{role}": "qwen-plus" for role in ("CHAT", "EXTRACT", "JUDGE", "BRIEF")},
    })
    get_settings.cache_clear()


if __name__ == "__main__":
    configure()
    from polaris.cli import app

    sys.argv = ["polaris", "daemon", "--log", str(ROOT / "data/logs/daemon.log")]
    app()
