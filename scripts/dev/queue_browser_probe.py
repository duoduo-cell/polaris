"""真实独立API、真实模型、独立无头浏览器：验证跨模块、刷新、关闭页面后的任务与原话。"""
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from playwright.sync_api import expect, sync_playwright
from sqlalchemy.engine import make_url

from polaris.config import get_settings

s = get_settings()
if make_url(s.database_url).database != "polaris_eval_queue_20261013":
    raise ValueError("只允许独立队列评测库，不操作生产聊天")
destination = Path("data/eval/real_tasks/queue-browser-20261013.json")
if destination.exists():
    raise ValueError("不能覆盖原测试记录")
auth = {"Authorization": f"Bearer {s.owner_token.get_secret_value()}"}
thread = f"queue-browser-{uuid4().hex}"
question = "请从本地资料库查询腾讯Agent实习岗位，列出名称、主要职责、学历和论文要求、每周天数是否明确写了及原链接。不要现查网站。"
client = httpx.Client(base_url="http://127.0.0.1:8001", headers=auth, timeout=30)
record = {"thread": thread, "question": question, "real_model": True, "real_api_port": 8001,
          "database": "polaris_eval_queue_20261013", "requests": [], "checks": {}}


def snapshot():
    response = client.get(f"/api/me/chat/{thread}/messages")
    response.raise_for_status()
    return response.json()


try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context()
        context.add_init_script(f"localStorage.setItem('polaris-thread', {json.dumps(thread)});")

        def forward(route):
            url = urlsplit(route.request.url)
            path = url.path.split("/api/", 1)[1]
            record["requests"].append({"method": route.request.method, "path": path})
            response = client.request(route.request.method, f"/api/me/{path}" + (f"?{url.query}" if url.query else ""),
                                      content=route.request.post_data,
                                      headers={"Content-Type": "application/json"})
            route.fulfill(status=response.status_code, body=response.content,
                          content_type=response.headers.get("content-type", "application/json"))

        context.route("**/api/**", forward)
        page = context.new_page()
        page.goto("http://127.0.0.1:3000/chat")
        expect(page.get_by_role("textbox")).to_be_enabled()
        page.get_by_role("textbox").fill(question)
        page.get_by_role("textbox").press("Enter")
        expect(page.get_by_test_id("chat-transcript").get_by_text(question, exact=True)).to_have_count(1)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            first = snapshot()
            if first["tasks"] and first["tasks"][0]["state"] == "running":
                break
            page.wait_for_timeout(250)
        assert first["tasks"][0]["state"] == "running", "未观测到运行中，不能证明中途离开有效"
        task_id = first["tasks"][0]["id"]
        page.locator("nav").get_by_role("link", name="记忆", exact=True).click()
        page.reload()
        away = snapshot()
        assert away["tasks"][0]["id"] == task_id
        assert away["tasks"][0]["state"] in ("running", "completed")
        record["checks"]["navigation_and_reload_kept_task"] = True
        page.close()
        closed = snapshot()
        record["state_after_page_close"] = closed["tasks"][0]["state"]
        page = context.new_page()
        page.goto("http://127.0.0.1:3000/chat")
        expect(page.get_by_test_id("chat-transcript").get_by_text(question, exact=True)).to_have_count(1)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            final = snapshot()
            if final["tasks"][0]["state"] not in ("queued", "running"):
                break
            page.wait_for_timeout(1000)
        task = final["tasks"][0]
        assert task["state"] == "completed" and not task["error"], "真实任务未完成"
        assert task["id"] == task_id
        assert [m["role"] for m in final["messages"]] == ["user", "assistant"]
        assert final["messages"][0]["content"] == question
        answer = final["messages"][1]["content"]
        assert answer and any(c["ok"] and c["tool"] == "jobs_search" for c in task["result"]["tool_calls"])
        assert not any(c["tool"] in ("platform_search", "source_open") for c in task["result"]["tool_calls"])
        first_line = re.sub(r"[#*`]", "", answer).splitlines()[0].strip()
        expect(page.get_by_test_id("chat-transcript")).to_contain_text(first_line, timeout=15000)
        page.reload()
        expect(page.get_by_test_id("chat-transcript").get_by_text(question, exact=True)).to_have_count(1)
        assert len(snapshot()["messages"]) == 2
        record["checks"].update(page_close_reconnect_completed=True, original_message_retained=True,
                                 no_duplicate_answer=True, real_tool_executed=True, no_live_browser_used=True)
        record["snapshot"] = final
        browser.close()
except Exception as exc:  # noqa: BLE001
    record["error_type"] = type(exc).__name__
    raise
finally:
    client.close()
    destination.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checks": record["checks"], "error_type": record.get("error_type")}), flush=True)
