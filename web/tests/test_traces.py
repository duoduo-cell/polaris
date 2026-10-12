"""追踪：单次回答的时间线和「追踪」页的浏览器回归，模拟接口，无真实模型或数据库写入。"""
import unittest
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

NOW = "2026-10-12T10:00:00+08:00"
TRACE = {"model_calls": [{"at_ms": 0, "ms": 9000, "kind": "tools", "tool_calls": 2, "input_tokens": 12000, "output_tokens": 300,
                          "reasoning_tokens": 0, "cached_tokens": 8000, "cost_usd": 0.001, "model": "合成模型"},
                         {"at_ms": 12500, "ms": 8000, "kind": "answer", "tool_calls": 0, "input_tokens": 15000, "output_tokens": 900,
                          "reasoning_tokens": 0, "cached_tokens": 11000, "cost_usd": 0.002, "model": "合成模型"}],
         "tools": [{"tool": "jobs_search", "at_ms": 9100, "ms": 2400, "ok": True, "error_kind": None, "live": False},
                   {"tool": "questions_search", "at_ms": 9100, "ms": 3300, "ok": False, "error_kind": "invalid_args", "live": False}],
         "totals": {"model_calls": 2, "model_ms": 17000, "input_tokens": 27000, "cached_tokens": 19000, "output_tokens": 1200,
                    "reasoning_tokens": 0, "cost_usd": 0.003, "tool_calls": 2, "tool_ms": 5700, "tool_errors": 1}}
TURN = {"answer": "合成回答。", "results": [], "memory_events": [], "tool_calls": [], "latency_ms": 20500, "trace": TRACE,
        "activities": [{"tool": "jobs_search", "args": {}, "ok": True, "returned": 3, "sources": ["官网"]}]}
OVERVIEW = {"days": 7, "answers": 3, "latency": {"p50": 20.5, "p90": 41.0, "max": 52.3},
            "per_answer": {"model_calls": 2.7, "tool_calls": 3.0, "input_tokens": 27000, "output_tokens": 1200}, "cache_hit": 0.7,
            "tools": {"calls": 9, "errors": 1, "error_kinds": {"invalid_args": 1},
                      "by_tool": [{"tool": "jobs_search", "calls": 5, "errors": 0, "p50_ms": 2400, "error_kinds": {}},
                                  {"tool": "questions_search", "calls": 4, "errors": 1, "p50_ms": 3300,
                                   "error_kinds": {"invalid_args": 1}}]},
            "tasks": {"completed": 3}, "compacted": 0, "live_lookups": 0, "memory_extract_p50_ms": 9000,
            "recent": [{"id": 2, "thread_id": "t-other", "at": NOW, "question": "合成问题甲", "seconds": 20.5, "model_calls": 2,
                        "tool_calls": 2, "tool_errors": 1, "input_tokens": 27000, "output_tokens": 1200, "compacted": False, "live": 0}]}


class TracesRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel="msedge", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.paths = []
        self.empty = False
        self.context = self.browser.new_context()
        self.context.route("**/api/**", self.mock_api)
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    def mock_api(self, route):
        url = urlparse(route.request.url)
        path = url.path.split("/api/", 1)[1]
        self.paths.append(f"{path}?{url.query}" if url.query else path)
        if path.endswith("/messages"):
            out = {"messages": [{"id": 1, "role": "user", "content": "合成问题甲"},
                                {"id": 2, "role": "assistant", "content": TURN["answer"], "turn": TURN}], "approvals": [], "tasks": []}
        elif path == "chat/threads":
            out = [{"id": "t-other", "title": "合成问题甲", "active": False, "waiting_approval": False}]
        elif path.startswith("me/traces") or path.startswith("traces"):
            out = {**OVERVIEW, "answers": 0, "recent": []} if self.empty else OVERVIEW
        else:
            out = {}
        route.fulfill(json=out)

    def test_answer_shows_its_timeline(self):
        self.page.goto("http://localhost:3000/chat")
        self.page.get_by_role("button", name="思考了 20.5 秒").click()
        self.page.get_by_text("查看追踪").first.click()
        trace = self.page.get_by_test_id("trace").first
        expect(trace).to_be_visible()
        expect(trace).to_contain_text("2 次")
        expect(trace).to_contain_text("参数")

    def test_overview_page_and_range(self):
        self.page.goto("http://localhost:3000/traces")
        expect(self.page.get_by_text("20.5s / 41s")).to_be_visible()
        expect(self.page.get_by_text("合成问题甲")).to_be_visible()
        self.page.get_by_role("radio", name="30 天").click()
        self.page.wait_for_timeout(300)
        self.assertTrue(any("days=30" in p for p in self.paths), self.paths)

    def test_recent_answer_opens_its_thread(self):
        self.page.goto("http://localhost:3000/traces")
        self.page.get_by_text("合成问题甲").click()
        self.page.wait_for_url("**/chat")
        self.page.wait_for_timeout(300)
        self.assertTrue(any(p.startswith("chat/t-other/messages") for p in self.paths), self.paths)

    def test_empty_range_says_so(self):
        self.empty = True
        self.page.goto("http://localhost:3000/traces")
        expect(self.page.get_by_text("这段时间还没有回答")).to_be_visible()


if __name__ == "__main__":
    unittest.main(verbosity=2)
