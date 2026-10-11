"""浏览器回归：模拟持久化后端，导航、刷新、关页、排队、审批和重连不调用真实模型。"""
import unittest
from datetime import UTC, datetime

from playwright.sync_api import expect, sync_playwright


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
        self.threads = {}
        self.requests = []
        self.next_message = 1
        self.fail_reads = False
        self.lose_response = False
        self.context = self.browser.new_context()
        self.context.route("**/api/**", self.mock_api)
        self.page = self.context.new_page()
        self.page.goto("http://localhost:3000/chat")
        expect(self.page.get_by_role("textbox")).to_be_enabled()

    def tearDown(self):
        self.context.close()

    def data(self, thread="main"):
        return self.threads.setdefault(thread, {"messages": [], "approvals": [], "tasks": []})

    def add_message(self, data, role, content, turn=None):
        data["messages"].append({"id": self.next_message, "role": role, "content": content, **({"turn": turn} if turn else {})})
        self.next_message += 1

    def mock_api(self, route):
        path = route.request.url.split("/api/", 1)[1].split("?", 1)[0]
        if route.request.method == "POST" and (path == "chat/tasks" or path.endswith("/resume/tasks")):
            body = route.request.post_data_json
            self.requests.append(body)
            thread = body["thread_id"]
            data = self.data(thread)
            task = next((t for t in data["tasks"] if t["id"] == body["request_id"]), None)
            if task is None:
                now = datetime.now(UTC).isoformat()
                task = {"id": body["request_id"], "kind": "chat" if "message" in body else "resume",
                        "state": "queued" if any(t["state"] == "running" for t in data["tasks"]) else "running",
                        "activities": [], "result": None, "error": None, "created_at": now, "started_at": now}
                data["tasks"].append(task)
                if "message" in body:
                    self.add_message(data, "user", body["message"])
                else:
                    for prior in data["tasks"][:-1]:
                        if prior["state"] == "waiting_approval":
                            prior["state"] = "continued"
            if self.lose_response:
                self.lose_response = False
                route.fulfill(status=502, json={"detail": "模拟发送响应丢失"})
            else:
                route.fulfill(status=202, json=task)
            return
        if path.endswith("/messages"):
            if self.fail_reads:
                route.fulfill(status=502, json={"detail": "模拟断网"})
            else:
                route.fulfill(json=self.data(path.split("/")[1]))
            return
        if path == "chat/threads":
            route.fulfill(json=[{"id": thread, "title": data["messages"][0]["content"][:40],
                                "active": any(t["state"] in ("queued", "running") for t in data["tasks"]),
                                "waiting_approval": bool(data["approvals"])} for thread, data in self.threads.items() if data["messages"]])
            return
        payload = {"memory": {"on": "2026-10-11", "facts": [], "waiting_for_confirmation": []},
                   "brief": {"markdown": None}, "notifications": [], "feedback": {"ok": True}}.get(path, {})
        route.fulfill(json=payload)

    def send(self, question="导航测试：推荐一个岗位"):
        count = len(self.requests)
        self.page.get_by_role("textbox").fill(question)
        self.page.get_by_role("textbox").press("Enter")
        expect(self.page.get_by_test_id("chat-transcript").get_by_text(question, exact=True)).to_be_visible()
        self.assertGreater(len(self.requests), count)

    def finish(self, answer="测试结果：J-123", approvals=None, thread="main", index=0, error=None):
        data = self.data(thread)
        task = data["tasks"][index]
        turn = {"answer": answer, "memory_events": [{"op": "add", "attribute": "school", "value": "某大学", "reason": "原话"}],
                "memory_questions": [], "approvals": approvals or [],
                "tool_calls": [{"tool": "jobs_search", "args": {"query": "private-tool-args"}, "ok": True}],
                "latency_ms": 1000, "error": error}
        task.update(state="waiting_approval" if approvals else "failed" if error else "completed", result=turn, error=error)
        data["approvals"] = approvals or []
        if answer and not approvals:
            self.add_message(data, "assistant", answer, turn)
        for pending in data["tasks"]:
            if pending["state"] == "queued" and not approvals:
                pending["state"] = "running"
                break

    def navigate(self, label):
        self.page.locator("nav").get_by_role("link", name=label, exact=True).click()

    def test_navigation_keeps_raw_message_and_activity_is_collapsed(self):
        self.send()
        self.data()["tasks"][0]["activities"] = [{"type": "tool", "tool": "jobs_search", "args": {"query": "private-tool-args"}}]
        self.navigate("记忆")
        self.navigate("对话")
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("导航测试：推荐一个岗位", exact=True)).to_be_visible()
        expect(self.page.get_by_role("status")).to_contain_text("正在查找")
        expect(self.page.get_by_test_id("chat-transcript").get_by_text('{"query":"private-tool-args"}', exact=True)).not_to_be_visible()
        self.finish()
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("测试结果：J-123", exact=True)).to_have_count(1)
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("记下：某大学", exact=True)).not_to_be_visible()
        self.page.get_by_test_id("chat-transcript").get_by_text("查看活动 · 1.0 秒", exact=True).click()
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("记下：某大学", exact=True)).to_be_visible()

    def test_completion_while_away_appears_once(self):
        self.send()
        self.navigate("今天")
        self.finish()
        self.navigate("对话")
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("测试结果：J-123", exact=True)).to_have_count(1)
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("导航测试：推荐一个岗位", exact=True)).to_have_count(1)

    def test_reload_recovers_running_task_and_finished_result(self):
        self.send("刷新测试原话")
        self.page.reload()
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("刷新测试原话", exact=True)).to_have_count(1)
        expect(self.page.get_by_role("status")).to_be_visible()
        self.finish()
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("测试结果：J-123", exact=True)).to_have_count(1)
        self.page.reload()
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("测试结果：J-123", exact=True)).to_have_count(1)
        self.assertEqual(len(self.requests), 1)

    def test_close_browser_and_reopen_recovers_result(self):
        self.send("关页测试")
        state = self.context.storage_state()
        self.context.close()
        self.finish()
        self.context = self.browser.new_context(storage_state=state)
        self.context.route("**/api/**", self.mock_api)
        self.page = self.context.new_page()
        self.page.goto("http://localhost:3000/chat")
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("关页测试", exact=True)).to_have_count(1)
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("测试结果：J-123", exact=True)).to_have_count(1)

    def test_can_queue_more_words_without_losing_current_work(self):
        self.send("第一句话")
        expect(self.page.get_by_role("textbox")).to_be_enabled()
        self.send("第二句话")
        expect(self.page.get_by_role("status")).to_contain_text("1 条消息在排队")
        self.finish("第一条回复")
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("第一条回复", exact=True)).to_be_visible()
        expect(self.page.get_by_role("status")).to_be_visible()
        self.finish("第二条回复", index=1)
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("第二条回复", exact=True)).to_have_count(1)
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("第二句话", exact=True)).to_have_count(1)

    def test_new_thread_keeps_old_task_and_can_switch_back(self):
        self.send("旧会话后台工作")
        self.page.get_by_role("button", name="新对话").click()
        expect(self.page.get_by_role("textbox")).to_be_enabled()
        self.send("新会话问题")
        new_thread = self.requests[-1]["thread_id"]
        self.assertNotEqual(new_thread, "main")
        self.finish("旧会话结果")
        self.page.get_by_label("切换对话").select_option("main")
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("旧会话结果", exact=True)).to_be_visible()
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("新会话问题", exact=True)).to_have_count(0)

    def test_approval_survives_reload_then_resume_once(self):
        self.send("忘掉学校")
        self.finish("", approvals=[{"name": "memory_forget", "args": {"attribute": "school"}, "description": "是否忘掉学校？"}])
        expect(self.page.get_by_role("button", name="拒绝", exact=True)).to_be_visible()
        self.page.reload()
        expect(self.page.get_by_role("textbox")).to_be_disabled()
        self.page.get_by_role("button", name="拒绝", exact=True).click()
        expect(self.page.get_by_role("button", name="拒绝", exact=True)).to_be_disabled()
        self.navigate("记忆")
        self.finish("好，保留学校", index=1)
        self.navigate("对话")
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("好，保留学校", exact=True)).to_have_count(1)
        expect(self.page.get_by_role("textbox")).to_be_enabled()
        self.assertEqual(len(self.requests), 2)
        self.assertFalse(self.requests[-1]["approve"])

    def test_draft_survives_navigation_and_reload(self):
        self.page.get_by_role("textbox").fill("尚未发送的草稿")
        self.navigate("记忆")
        self.navigate("对话")
        expect(self.page.get_by_role("textbox")).to_have_value("尚未发送的草稿")
        self.page.reload()
        expect(self.page.get_by_role("textbox")).to_have_value("尚未发送的草稿")
        self.assertEqual(self.requests, [])

    def test_lost_submit_response_retries_same_request_without_duplicate(self):
        self.lose_response = True
        self.send("响应丢失测试")
        expect(self.page.get_by_role("status")).to_be_visible()
        self.assertEqual(len(self.data()["tasks"]), 1)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[0]["request_id"], self.requests[1]["request_id"])
        self.finish()
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("响应丢失测试", exact=True)).to_have_count(1)
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("测试结果：J-123", exact=True)).to_have_count(1)

    def test_temporary_disconnect_recovers_and_keeps_result(self):
        self.send("断网测试")
        self.fail_reads = True
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("模拟断网", exact=False)).to_be_visible()
        self.finish()
        self.fail_reads = False
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("测试结果：J-123", exact=True)).to_have_count(1)
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("模拟断网", exact=False)).to_have_count(0)

    def test_failure_survives_reload_and_next_send_works(self):
        self.send("会失败的请求")
        self.finish("", error="模拟任务失败")
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("模拟任务失败", exact=True)).to_be_visible()
        self.page.reload()
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("模拟任务失败", exact=True)).to_be_visible()
        self.send("下一次请求")
        self.finish("下一次成功", index=1)
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("下一次成功", exact=True)).to_be_visible()


if __name__ == "__main__":
    unittest.main(verbosity=2)
