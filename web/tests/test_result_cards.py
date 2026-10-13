"""卡片、详情、来源和独立岗位进度的浏览器回归，无真实模型或数据库写入。"""
import copy
import unittest
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright


class ResultCardsRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel="msedge", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.requests = []
        self.rows = [{"id": f"J-{i}", "kind": "job", "display_title": f"合成公司 · 岗位{i}", "company": "合成公司",
                      "title": f"岗位{i}", "city": "北京", "type": "实习", "jd_text": "合成完整岗位详情",
                      "sources": [{"source": "xhs", "label": "小红书", "url": f"https://www.xiaohongshu.com/explore/test{i}",
                                   "access": "may_require_login", "fetched_at": "2026-10-12"}],
                      "followup": {"saved": False, "status": "untracked", "stage": None, "note": None,
                                   "next_followup": None, "revision": 0, "history": []}}
                     for i in range(1, 4)]
        self.context = self.browser.new_context()
        self.context.route("**/api/**", self.mock_api)
        self.page = self.context.new_page()
        self.page.goto("http://localhost:3000/chat")
        expect(self.page.get_by_test_id("result-cards")).to_be_visible()

    def tearDown(self):
        self.context.close()

    def mock_api(self, route):
        url = urlparse(route.request.url)
        path = url.path.split("/api/", 1)[1]
        self.requests.append((route.request.method, path, parse_qs(url.query)))
        out = {}
        if path == "chat/main/messages":
            turn = {"answer": "推荐 J-1、J-2、J-3。", "results": self.rows, "memory_events": [], "tool_calls": [], "latency_ms": 1200,
                    "activities": [{"tool": "jobs_search", "args": {"company": "合成公司", "limit": 3}, "ok": True,
                                    "returned": 3, "sources": ["小红书"]}]}
            out = {"messages": [{"id": 1, "role": "user", "content": "推荐三个岗位"},
                                {"id": 2, "role": "assistant", "content": turn["answer"], "turn": turn}], "approvals": [], "tasks": []}
        elif path == "chat/threads":
            out = [{"id": "main", "title": "合成测试对话", "active": False, "waiting_approval": False}]
        elif path.startswith("results/J-"):
            out = self.rows[int(path.rsplit("-", 1)[1]) - 1]
        elif path.startswith("jobs/J-") and path.endswith("/followup"):
            row = self.rows[int(path.split("/")[1][2:]) - 1]
            before = copy.deepcopy(row["followup"])
            body = route.request.post_data_json
            self.assertEqual(body["revision"], before["revision"])
            row["followup"].update(body["changes"])
            row["followup"]["revision"] += 1
            row["followup"]["history"].append({"before": before, "after": {k: v for k, v in row["followup"].items() if k != "history"},
                                               "source_text": "用户明确操作", "created_at": "2026-10-12"})
            out = row["followup"]
        elif path.startswith("knowledge/"):
            out = {"items": [], "next_offset": None}
        elif path == "followups":
            out = [r for r in self.rows if r["followup"]["revision"]]
        elif path == "memory":
            out = {"facts": []}
        route.fulfill(json=out)

    def test_three_cards_show_names_sources_and_reading_does_not_apply(self):
        expect(self.page.locator('[data-testid^="result-J-"]')).to_have_count(3)
        expect(self.page.get_by_role("link", name="小红书", exact=True)).to_have_count(3)
        expect(self.page.get_by_test_id("chat-transcript").get_by_text("推荐 J-1、J-2、J-3。", exact=True)).to_have_count(0)
        card = self.page.get_by_test_id("result-J-1")
        card.get_by_role("button", name="查看详情").click()
        dialog = self.page.get_by_role("dialog", name="结果详情")
        expect(dialog.get_by_role("link", name="打开来源")).to_have_attribute("href", self.rows[0]["sources"][0]["url"])
        expect(dialog.get_by_label("投递状态")).to_have_value("untracked")
        self.page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
        self.assertFalse(any(method == "POST" for method, _, _ in self.requests))

    def test_unknown_skill_does_not_prefill_a_false_user_claim_and_update_is_labeled(self):
        self.rows[0]["match"] = {"score": 59, "level": "weak", "checks": [],
                                 "skills": {"required": ["Python", "PyTorch"], "have": ["Python"],
                                            "missing": ["PyTorch"], "not_stated": ["PyTorch"], "known": True}}
        self.rows[0]["sources"][0].update(published_at="2026-10-11T16:00:00+00:00", date_kind="updated")
        self.page.get_by_test_id("result-J-1").get_by_role("button", name="查看详情").click()
        dialog = self.page.get_by_role("dialog")
        expect(dialog.get_by_text("待确认 PyTorch", exact=True)).to_be_visible()
        expect(dialog.get_by_text("来源更新于 2026-10-12", exact=False)).to_be_visible()
        dialog.get_by_role("button", name="问北极星怎么自测", exact=True).click()
        composer = self.page.locator("textarea:visible")
        expect(composer).to_have_value(__import__("re").compile("掌握情况还没确认"))
        self.assertNotIn("我还不会", composer.input_value())
        self.assertFalse(any(method == "POST" for method, _, _ in self.requests))

    def test_bookmark_and_second_job_progress_survive_reload_separately(self):
        self.page.get_by_test_id("result-J-1").get_by_role("button", name="查看详情").click()
        dialog = self.page.get_by_role("dialog")
        dialog.get_by_label("岗位备注").fill("尚未保存的备注")
        dialog.get_by_role("button", name="收藏岗位", exact=True).click()
        expect(dialog.get_by_role("button", name="取消收藏", exact=True)).to_be_visible()
        expect(dialog.get_by_label("投递状态")).to_have_value("untracked")
        expect(dialog.get_by_label("岗位备注")).to_have_value("尚未保存的备注")
        dialog.get_by_role("button", name="关闭详情").click()
        self.page.get_by_test_id("result-J-2").get_by_role("button", name="查看详情").click()
        dialog.get_by_label("投递状态").select_option("applied")
        dialog.get_by_role("button", name="保存进度", exact=True).click()
        expect(self.page.get_by_test_id("result-J-2").get_by_text("已投递", exact=True)).to_be_visible()
        dialog.get_by_role("button", name="关闭详情").click()
        self.page.reload()
        expect(self.page.get_by_test_id("result-J-1").get_by_text("已收藏", exact=True)).to_be_visible()
        expect(self.page.get_by_test_id("result-J-1").get_by_text("已投递", exact=True)).not_to_be_visible()
        expect(self.page.get_by_test_id("result-J-2").get_by_text("已投递", exact=True)).to_be_visible()

    def test_knowledge_tabs_and_filters_request_correct_material(self):
        self.page.get_by_role("link", name="面经与内推", exact=True).click()
        expect(self.page.get_by_role("tab", name="面经", exact=True)).to_have_attribute("aria-selected", "true")
        self.page.get_by_role("tab", name="面试题", exact=True).click()
        self.page.get_by_label("公司", exact=True).fill("字节")
        self.page.get_by_label("面试轮次").select_option("2面")
        self.page.get_by_label("资料来源").select_option("xhs")
        self.page.get_by_role("button", name="筛选", exact=True).click()
        expect(self.page.get_by_role("button", name="筛选", exact=True)).to_be_enabled()
        params = next(p for _, path, p in reversed(self.requests) if path == "knowledge/questions")
        self.assertEqual(params["company"], ["字节"])
        self.assertEqual(params["round"], ["2面"])
        self.assertEqual(params["material"], ["firsthand"])
        self.page.get_by_role("tab", name="备考整理", exact=True).click()
        expect(self.page.get_by_role("button", name="筛选", exact=True)).to_be_enabled()
        params = next(p for _, path, p in reversed(self.requests) if path == "knowledge/posts")
        self.assertEqual(params["material"], ["study"])
        self.assertNotIn("round", params)

    def test_activity_explains_tool_source_and_count(self):
        transcript = self.page.get_by_test_id("chat-transcript")
        expect(transcript.get_by_text("搜岗位", exact=True)).not_to_be_visible()  # 思考过程默认收起
        transcript.get_by_text("思考了 1.2 秒 · 查了 1 次", exact=True).click()
        expect(transcript.get_by_text("搜岗位", exact=True)).to_be_visible()
        expect(transcript.get_by_text("· 找到 3 条", exact=True)).to_be_visible()
        expect(transcript.get_by_text("· 小红书", exact=True)).to_be_visible()
        transcript.get_by_text("查看筛选条件", exact=True).click()
        expect(transcript.get_by_text("公司：", exact=True)).to_be_visible()

    def test_answer_hides_ids_and_links_names_to_details(self):
        transcript = self.page.get_by_test_id("chat-transcript")
        expect(transcript.get_by_text("J-1", exact=False)).to_have_count(0)
        transcript.get_by_role("button", name="合成公司 · 岗位2", exact=True).click()
        expect(self.page.get_by_role("dialog", name="结果详情")).to_be_visible()
        expect(self.page.get_by_role("dialog").get_by_text("J-2", exact=False)).to_have_count(0)

    def test_match_shows_each_condition_and_missing_skill(self):
        self.rows[0]["match"] = {"score": 86, "level": "good", "checks": [
            {"key": "city", "label": "城市", "ok": True, "detail": "北京"},
            {"key": "days", "label": "每周天数", "ok": False, "detail": "要每周 5 天，你能去 4 天"},
            {"key": "months", "label": "实习时长", "ok": None, "detail": "没写实习多久"}],
            "skills": {"required": ["RAG/检索增强", "CUDA/算子"], "have": ["RAG/检索增强"], "missing": ["CUDA/算子"], "known": True}}
        self.page.reload()
        card = self.page.get_by_test_id("result-J-1")
        expect(card.get_by_text("要每周 5 天，你能去 4 天", exact=True)).to_be_visible()
        expect(card.get_by_text("1 项岗位没写", exact=True)).to_be_visible()
        expect(card.get_by_text("待确认 CUDA/算子", exact=True)).to_be_visible()
        card.get_by_role("button", name="查看详情").click()
        dialog = self.page.get_by_role("dialog", name="结果详情")
        expect(dialog.get_by_text("很合适", exact=True)).to_be_visible()
        dialog.get_by_role("button", name="问北极星怎么自测").click()
        expect(self.page.get_by_role("textbox")).to_have_value(
            "合成公司 · 岗位1 这个岗位要求 CUDA/算子，我的掌握情况还没确认。请给我自测题和答题要点，帮我判断需要补什么。")


    def test_chat_folds_question_references_into_a_list(self):
        self.rows.append({"id": "Q-7", "kind": "question", "display_title": "如何设计 Agent 记忆？", "question": "如何设计 Agent 记忆？",
                          "company": "合成公司", "round": "1面", "material_label": "面经记录", "sources": []})
        self.page.reload()
        expect(self.page.get_by_test_id("result-Q-7")).to_have_count(0)
        self.page.get_by_text("回答里引用了 1 道面试题，点开看题目和出处").click()
        expect(self.page.get_by_test_id("result-questions").get_by_text("如何设计 Agent 记忆？")).to_be_visible()
        self.page.get_by_test_id("result-questions").get_by_role("button", name="出处").click()
        expect(self.page.get_by_role("dialog", name="结果详情")).to_be_visible()


if __name__ == "__main__":
    unittest.main(verbosity=2)
