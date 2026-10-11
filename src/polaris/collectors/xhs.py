"""小红书（B 档）：通过本机运行的 xiaohongshu-mcp 服务读取。只放行只读工具，评论、点赞、收藏、发布一律屏蔽。

服务在你自己的电脑上跑（docker compose --profile local up -d xhs-mcp），用你本人的账号扫码登录。
"""
import asyncio
import base64
import os
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from polaris.config import ROOT

XHS_URL = os.environ.get("XHS_MCP_URL", "http://127.0.0.1:18060/mcp")
# 白名单：只读。发笔记、评论、点赞、收藏、回复通知等写操作，以及读你自己主页和通知的工具，都不在里面
READ_ONLY_TOOLS = frozenset({"check_login_status", "get_login_qrcode",
                             "search_feeds", "get_feed_detail", "user_profile", "list_feeds"})


class BlockedTool(PermissionError):
    pass


def _token() -> str | None:
    if os.environ.get("XHS_MCP_TOKEN"):
        return os.environ["XHS_MCP_TOKEN"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("XHS_MCP_TOKEN="):
                return line.split("=", 1)[1].strip() or None
    return None


async def _call(name: str, args: dict[str, Any] | None = None) -> list[Any]:
    if name not in READ_ONLY_TOOLS:
        raise BlockedTool(f"{name} 不是只读工具，北极星不调用它")
    token = _token()
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with streamablehttp_client(XHS_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, args or {})
            return list(result.content)


def call(name: str, args: dict[str, Any] | None = None) -> list[Any]:
    return asyncio.run(_call(name, args))


async def list_tools() -> list[dict[str, Any]]:
    token = _token()
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with streamablehttp_client(XHS_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return [{"name": t.name, "schema": t.inputSchema} for t in tools.tools]


def text_of(content: list[Any]) -> str:
    return "\n".join(getattr(c, "text", "") for c in content if getattr(c, "type", "") == "text")


def login_qrcode() -> tuple[bytes | None, str]:
    """返回 (二维码 PNG, 文字说明)。已经登录时没有二维码。"""
    content = call("get_login_qrcode")
    png = None
    for c in content:
        if getattr(c, "type", "") == "image":
            png = base64.b64decode(c.data)
    text = text_of(content)
    if png is None and "base64," in text:  # 有的版本把图片放在文字里
        png = base64.b64decode(text.split("base64,", 1)[1].split('"')[0].split()[0])
    return png, text


def login_status() -> str:
    return text_of(call("check_login_status"))
