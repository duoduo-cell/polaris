"""MCP 服务：起一个本地服务，用 langchain-mcp-adapters 当客户端连上去。"""
import asyncio
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from polaris.ingest import RawItemIn, ingest_raw_items


@pytest.fixture(scope="module")
def server(engine):
    """整个文件共用一个服务：MCP 的会话管理器在一个进程里只能启动一次。"""
    from polaris.api.app import app

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp/"
    srv.should_exit = True
    thread.join(timeout=5)


def test_mcp_lists_and_calls_read_only_tools(server, db):
    from langchain_mcp_adapters.client import MultiServerMCPClient

    rid = ingest_raw_items([RawItemIn(source="manual", raw_text="星槎科技 RAG 算法实习生")]).ids[0]

    async def go():
        client = MultiServerMCPClient({"polaris": {"transport": "streamable_http", "url": server,
                                                   "headers": {"Authorization": "Bearer test-mcp-token"}}})
        tools = {t.name: t for t in await client.get_tools()}
        out = await tools["raw_items_search"].ainvoke({"query": "星槎科技"})
        return sorted(tools), str(out)

    names, out = asyncio.run(go())
    assert names == ["jobs_get", "jobs_search", "jobs_trend", "kg_stats", "memory_card", "memory_history", "memory_known_at",
                     "peers_search", "posts_get", "posts_search", "questions_search", "raw_items_get", "raw_items_search"]
    # 只开放只读的：memory_forget 不在；现查（source_open、platform_search）用你本人登录的浏览器，也不在
    assert f"R-{rid}" in out


def test_mcp_requires_token(server, db):
    assert httpx.post(server, json={}).status_code == 401
    assert httpx.post(server, json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
