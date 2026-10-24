"""合成演示 API 的本机入口：数据库、口令都来自 POLARIS_ENV_FILE 指定的 .env.demo；
模型密钥只在本进程里从个人 .env 的百炼配置借用，不写进 .env.demo、不落盘。

用法（项目根目录）：
    $env:POLARIS_ENV_FILE = (Resolve-Path .env.demo).Path
    .venv\\Scripts\\python.exe -X utf8 scripts\\run_demo_bailian.py --port 8010
"""
import os
import sys

from polaris.config import get_settings


def configure() -> None:
    demo_file = os.environ.get("POLARIS_ENV_FILE")
    if not demo_file:
        raise SystemExit("先把 POLARIS_ENV_FILE 设成 .env.demo 的路径")
    del os.environ["POLARIS_ENV_FILE"]
    get_settings.cache_clear()
    key = get_settings().embed_key.get_secret_value()  # 个人配置里的百炼密钥，只读这一项
    os.environ["POLARIS_ENV_FILE"] = demo_file
    os.environ["LLM_API_KEY"] = key
    get_settings.cache_clear()
    if not get_settings().demo_mode:
        raise SystemExit("演示配置里 DEMO_MODE 必须为 true")


if __name__ == "__main__":
    configure()
    from polaris.cli import app

    sys.argv = ["polaris", "serve", *sys.argv[1:]]
    app()
