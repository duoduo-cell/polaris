"""手机版的浏览器回归：抽屉菜单、一屏内的对话输入框、其他页面能看到"正在回答"、可安装清单和图标、配对门。
模拟接口，无真实模型或数据库写入。配对门的几项要网页服务带着配对码启动：
  $env:POLARIS_PAIR_CODE='TEST-CODE'; npx next dev -H 127.0.0.1 --port 3000   （web 目录下）
  $env:POLARIS_TEST_PAIR_CODE='TEST-CODE'; .venv/Scripts/python.exe -X utf8 web/tests/test_mobile.py
不带配对码启动时这几项跳过，其余照常。"""
import os
import unittest
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

BASE = "http://localhost:3000"
CODE = os.environ.get("POLARIS_TEST_PAIR_CODE")
PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}
NOW = "2026-10-12T10:00:00+08:00"
RUNNING = {"id": "task-run", "kind": "chat", "state": "running", "created_at": NOW, "started_at": NOW, "result": None, "error": None,
           "activities": [{"type": "tool", "tool": "jobs_search", "desc": "搜岗位 · 北京", "args": {}}], "live": {"draft": "", "seq": 1}}


class Mobile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel="msedge", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.running = False
        self.context = self.browser.new_context(**PHONE)
        if CODE:
            self.context.request.get(f"{BASE}/pair?code={CODE}", max_redirects=0)  # 拿到配对 cookie
        self.context.route("**/api/**", self.mock_api)
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    def mock_api(self, route):
        path = urlparse(route.request.url).path.split("/api/", 1)[1]
        if path.endswith("/messages"):
            msgs = [{"id": 1, "role": "user", "content": "合成问题", "task_id": "task-run" if self.running else None}]
            out = {"messages": msgs, "approvals": [], "tasks": [RUNNING] if self.running else []}
        elif path == "chat/threads":
            out = [{"id": "main", "title": "合成问题", "active": self.running, "waiting_approval": False}]
        elif path.startswith("chat/tasks/"):
            out = RUNNING
        else:
            out = {}
        route.fulfill(json=out)

    def test_drawer_lists_every_page_and_navigates(self):
        self.page.goto(f"{BASE}/")
        self.page.get_by_role("button", name="打开菜单").click()
        drawer = self.page.get_by_role("dialog", name="菜单")
        expect(drawer).to_be_visible()
        for label in ("今天", "对话", "记忆", "岗位", "面经与内推", "图谱", "追踪", "评测"):
            expect(drawer.get_by_role("link", name=label)).to_be_visible()
        drawer.get_by_role("link", name="岗位").click()
        self.page.wait_for_url("**/jobs")
        expect(drawer).to_be_hidden()
        expect(self.page.locator("header > div").get_by_text("岗位", exact=True)).to_be_visible()

    def test_pages_fit_the_phone_width_and_chat_input_is_on_screen(self):
        for path in ("/", "/chat", "/memory", "/jobs", "/interviews", "/graph", "/traces", "/evals"):
            self.page.goto(f"{BASE}{path}")
            self.page.wait_for_timeout(400)
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), 390, path)
        self.page.goto(f"{BASE}/chat")
        box = self.page.get_by_role("textbox").last.bounding_box()
        self.assertIsNotNone(box)
        self.assertLessEqual(box["y"] + box["height"], 844)  # 不用往下滚就能打字

    def test_other_pages_show_that_an_answer_is_running(self):
        self.running = True
        self.page.goto(f"{BASE}/jobs")
        pill = self.page.get_by_role("link", name="正在回答")
        expect(pill).to_be_visible()
        pill.click()
        self.page.wait_for_url("**/chat")

    def test_desktop_keeps_the_sidebar(self):
        page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        if CODE:
            page.context.request.get(f"{BASE}/pair?code={CODE}", max_redirects=0)
        page.route("**/api/**", self.mock_api)
        page.goto(f"{BASE}/")
        expect(page.get_by_role("button", name="打开菜单")).to_be_hidden()
        expect(page.get_by_role("navigation", name="页面").get_by_role("link", name="追踪")).to_be_visible()
        page.close()

    def test_installable_manifest_and_icons(self):
        manifest = self.context.request.get(f"{BASE}/manifest.webmanifest").json()
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/chat")
        for icon in manifest["icons"]:
            r = self.context.request.get(f"{BASE}{icon['src']}")
            self.assertEqual(r.headers["content-type"], "image/png", icon)
            self.assertEqual(r.body()[:4], b"\x89PNG")
        self.assertEqual(self.context.request.get(f"{BASE}/sw.js").status, 200)


@unittest.skipUnless(CODE, "网页服务没带配对码启动，跳过配对门")
class PairGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel="msedge", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context(**PHONE)
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    def test_unpaired_devices_get_nothing(self):
        self.page.goto(f"{BASE}/memory")
        expect(self.page.get_by_role("heading", name="配对这台设备")).to_be_visible()  # 原地显示配对页，看不到记忆
        expect(self.page.get_by_text("关于你")).to_have_count(0)
        api = self.context.request.get(f"{BASE}/api/memory")
        self.assertEqual(api.status, 401)
        self.assertIn("还没配对", api.json()["detail"])
        # 图标、清单不含个人数据，不用配对（iOS 取清单时不带 cookie）
        self.assertEqual(self.context.request.get(f"{BASE}/manifest.webmanifest").status, 200)

    def test_wrong_code_is_refused_and_scanning_the_qr_pairs(self):
        self.page.goto(f"{BASE}/pair")
        self.page.get_by_role("textbox").fill("WRONG-CODE")
        self.page.get_by_role("button", name="配对").click()
        expect(self.page.get_by_text("配对码不对")).to_be_visible()
        self.assertEqual(self.context.request.get(f"{BASE}/api/memory").status, 401)
        self.page.goto(f"{BASE}/pair?code={CODE.lower()}")  # 扫码进来；大小写、短横线都不影响
        self.page.wait_for_url("**/chat")
        self.assertNotIn("code=", self.page.url)  # 地址栏里不留配对码
        cookie = next(c for c in self.context.cookies() if c["name"] == "polaris_pair")
        self.assertTrue(cookie["httpOnly"])
        self.assertNotIn(CODE.replace("-", ""), cookie["value"])  # 只存哈希
        self.assertNotEqual(self.context.request.get(f"{BASE}/api/memory").status, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
