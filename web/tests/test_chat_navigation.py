"""网页回归：用模拟事件流验证切换模块，不连接真实模型或个人数据。

先启动网页服务，再在仓库根目录运行：
  .venv/Scripts/python.exe -X utf8 web/tests/test_chat_navigation.py
"""
import unittest

from playwright.sync_api import expect, sync_playwright

MOCK_STREAM = r"""(() => {
  const realFetch = window.fetch.bind(window);
  window.chatTest = { requests: [], streams: [] };
  window.fetch = (input, init) => {
    const url = typeof input === 'string' ? input : input.url;
    if (url === '/api/chat/stream' || /\/api\/chat\/[^/]+\/resume\/stream$/.test(url)) {
      const test = window.chatTest;
      test.requests.push({ url, body: JSON.parse(init.body) });
      return Promise.resolve(new Response(new ReadableStream({
        start(controller) { test.streams.push(controller); }
      }), { headers: { 'Content-Type': 'text/event-stream' } }));
    }
    return realFetch(input, init);
  };
})();
"""


class ChatNavigation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel="msedge", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.context.add_init_script(MOCK_STREAM)
        self.context.route("**/api/**", self.mock_api)
        self.page = self.context.new_page()
        self.page.goto("http://localhost:3000/chat")
        expect(self.page.get_by_role("textbox")).to_be_enabled()

    def tearDown(self):
        self.context.close()

    def mock_api(self, route):
        path = route.request.url.split("/api/", 1)[1].split("?", 1)[0]
        payload = {"messages": [], "approvals": []} if path.endswith("/messages") else {
            "memory": {"on": "2026-10-11", "facts": [], "waiting_for_confirmation": []},
            "brief": {"markdown": None},
            "notifications": [],
            "feedback": {"ok": True},
        }.get(path, {})
        route.fulfill(json=payload)

    def send(self, question="导航测试：推荐一个岗位"):
        self.page.get_by_role("textbox").fill(question)
        self.page.get_by_role("textbox").press("Enter")
        self.page.wait_for_function("window.chatTest.requests.length > 0")

    def emit(self, event, index=0):
        self.page.evaluate("""({event, index}) => window.chatTest.streams[index].enqueue(
            new TextEncoder().encode('data: ' + JSON.stringify(event) + '\\n\\n'))""", {"event": event, "index": index})

    def finish(self, answer="测试结果：J-123", approvals=None, index=0):
        self.emit({"type": "done", "turn": {
            "answer": answer, "memory_events": [], "memory_questions": [],
            "approvals": approvals or [], "tool_calls": [{"tool": "jobs_search", "args": {}, "ok": True}],
            "latency_ms": 1000, "error": None,
        }}, index)
        self.page.evaluate("index => window.chatTest.streams[index].close()", index)

    def navigate(self, label):
        self.page.locator("nav").get_by_role("link", name=label, exact=True).click()

    def test_progress_and_pending_question_survive_navigation(self):
        self.send()
        self.emit({"type": "memory", "events": []})
        self.emit({"type": "tool", "tool": "jobs_search"})
        self.navigate("记忆")
        self.emit({"type": "token", "text": "正在保留的部分回答"})
        self.navigate("对话")
        expect(self.page.get_by_text("导航测试：推荐一个岗位", exact=True)).to_be_visible()
        expect(self.page.get_by_text("正在保留的部分回答", exact=True)).to_be_visible()
        expect(self.page.get_by_role("textbox")).to_be_disabled()
        expect(self.page.get_by_role("button", name="新对话")).to_be_disabled()
        self.navigate("今天")
        self.navigate("对话")
        expect(self.page.get_by_text("正在保留的部分回答", exact=True)).to_be_visible()
        self.assertEqual(len(self.page.evaluate("window.chatTest.requests")), 1)
        self.finish()
        expect(self.page.get_by_role("textbox")).to_be_enabled()

    def test_completion_while_away_is_shown_once_and_next_send_works(self):
        self.send()
        self.navigate("记忆")
        self.finish()
        self.navigate("对话")
        expect(self.page.get_by_text("测试结果：J-123", exact=True)).to_have_count(1)
        expect(self.page.get_by_text("导航测试：推荐一个岗位", exact=True)).to_have_count(1)
        expect(self.page.get_by_role("textbox")).to_be_enabled()
        self.send("继续比较一下")
        self.page.wait_for_function("window.chatTest.requests.length === 2")
        self.finish(answer="第二轮回答", index=1)
        expect(self.page.get_by_text("第二轮回答", exact=True)).to_be_visible()

    def test_approval_and_resume_survive_navigation(self):
        self.send("忘掉测试学校")
        self.navigate("记忆")
        self.finish(answer="", approvals=[{"name": "memory_forget", "args": {"attribute": "school"},
                                            "description": "忘掉测试学校"}])
        self.navigate("对话")
        expect(self.page.get_by_role("button", name="批准", exact=True)).to_be_visible()
        expect(self.page.get_by_role("textbox")).to_be_disabled()
        self.page.get_by_role("button", name="拒绝", exact=True).click()
        self.page.wait_for_function("window.chatTest.requests.length === 2")
        self.navigate("今天")
        self.finish(answer="已经拒绝", index=1)
        self.navigate("对话")
        expect(self.page.get_by_text("已经拒绝", exact=True)).to_be_visible()
        expect(self.page.get_by_role("textbox")).to_be_enabled()

    def test_draft_and_feedback_survive_navigation(self):
        self.page.get_by_role("textbox").fill("还没有发送的草稿")
        self.navigate("记忆")
        self.navigate("对话")
        expect(self.page.get_by_role("textbox")).to_have_value("还没有发送的草稿")
        self.send()
        self.finish()
        self.page.get_by_role("button", name="对", exact=True).click()
        expect(self.page.get_by_text("你标了：对", exact=True)).to_be_visible()
        self.navigate("今天")
        self.navigate("对话")
        expect(self.page.get_by_text("你标了：对", exact=True)).to_be_visible()
        self.page.get_by_role("button", name="新对话").click()
        first_thread = self.page.get_by_text("会话 t-", exact=False).inner_text()
        self.page.get_by_role("button", name="新对话").click()
        self.assertNotEqual(first_thread, self.page.get_by_text("会话 t-", exact=False).inner_text())
        expect(self.page.get_by_text("测试结果：J-123", exact=True)).to_have_count(0)

    def test_rapid_submit_is_single_and_error_while_away_is_retained(self):
        self.page.get_by_role("textbox").fill("只发送一次")
        self.page.get_by_role("textbox").evaluate("""el => {
            for (let i = 0; i < 2; i++) el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
        }""")
        self.page.wait_for_function("window.chatTest.requests.length === 1")
        self.navigate("记忆")
        self.emit({"type": "error", "message": "模拟服务暂时不可用"})
        self.page.evaluate("window.chatTest.streams[0].close()")
        self.navigate("对话")
        expect(self.page.get_by_text("模拟服务暂时不可用", exact=True)).to_be_visible()
        expect(self.page.get_by_text("只发送一次", exact=True)).to_have_count(1)
        expect(self.page.get_by_role("textbox")).to_be_enabled()
        self.assertEqual(len(self.page.evaluate("window.chatTest.requests")), 1)

    def test_stream_that_ends_without_result_is_not_silently_lost(self):
        self.send()
        self.navigate("今天")
        self.page.evaluate("window.chatTest.streams[0].close()")
        self.navigate("对话")
        expect(self.page.get_by_text("连接提前结束，没有收到完整结果。请检查后端状态。", exact=True)).to_be_visible()
        expect(self.page.get_by_text("导航测试：推荐一个岗位", exact=True)).to_be_visible()
        expect(self.page.get_by_role("textbox")).to_be_enabled()

    def test_saved_thread_is_used_after_browser_initialization(self):
        self.page.evaluate("localStorage.setItem('polaris-thread', 'saved-navigation-test')")
        self.page.reload()
        expect(self.page.get_by_role("textbox")).to_be_enabled()
        self.send()
        self.assertEqual(self.page.evaluate("window.chatTest.requests[0].body.thread_id"), "saved-navigation-test")
        self.finish()
        self.navigate("记忆")
        self.navigate("对话")
        expect(self.page.get_by_text("会话 saved-navigation-test", exact=True)).to_be_visible()
        expect(self.page.get_by_text("测试结果：J-123", exact=True)).to_be_visible()


if __name__ == "__main__":
    unittest.main(verbosity=2)
