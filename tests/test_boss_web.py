"""Boss 浏览器采集：只读页面自己加载的列表、只开大厂详情、被拦立刻停。用假页面，不联网。"""
from contextlib import contextmanager
from types import SimpleNamespace

from polaris.collectors.base import Query
from polaris.collectors.boss_web import BossWebCollector, blocked, description_from_page, search_url

DETAIL = "Agent 实习生\n职位描述\n负责 Agent 工具调用和评测。\n要求：熟悉 Python。\n公司介绍\n字节跳动是……"


class FakePage:
    def __init__(self, joblist, detail_text=DETAIL, block_after=None):
        self.url, self.joblist, self.detail_text, self.block_after = "about:blank", joblist, detail_text, block_after
        self.visits: list[str] = []

    @contextmanager
    def expect_response(self, predicate, timeout=0):
        yield SimpleNamespace(value=SimpleNamespace(json=lambda: {"code": 0, "zpData": {"jobList": self.joblist}}))

    def goto(self, url, wait_until=None):
        self.visits.append(url)
        if self.block_after is not None and len(self.visits) > self.block_after:
            self.url = "https://www.zhipin.com/web/user/safe/verify-slider?callbackUrl=x"
        else:
            self.url = url

    def wait_for_timeout(self, ms):
        pass

    def inner_text(self, selector):
        return self.detail_text if "job_detail" in self.url else "搜索结果"


def collector(page, **kw):
    ctx = SimpleNamespace(new_page=lambda: page)
    return BossWebCollector(ctx, cities=("北京",), scales=("10000人以上",), known=set(), sleep=lambda s: None, **kw)


CARDS = [{"encryptJobId": "J1", "jobName": "Agent 实习生", "brandName": "字节跳动", "bossName": "张三"},
         {"encryptJobId": "J2", "jobName": "AI 实习", "brandName": "某某小科技"}]


def test_reads_page_list_and_opens_only_big_companies():
    page = FakePage(CARDS)
    c = collector(page)
    items = list(c.collect(Query(keywords=["Agent 实习"])))
    assert [i.external_id for i in items] == ["J1"]
    assert "负责 Agent 工具调用和评测" in items[0].raw_text and "字节跳动是" not in items[0].raw_text
    assert "张三" not in str(items[0].raw_json) and items[0].fetch_method == "browser"
    assert page.visits == [search_url("Agent 实习", "北京", "10000人以上"), "https://www.zhipin.com/job_detail/J1.html"]
    assert c.stats.skipped_small == 1


def test_stops_on_verification_page():
    page = FakePage(CARDS, block_after=1)  # 第二个页面（详情）被跳到验证页
    c = collector(page)
    assert list(c.collect(Query(keywords=["Agent 实习", "RAG 实习"]))) == []
    assert c.stats.stopped and "疑似被拦" in c.stats.stopped and len(page.visits) == 2  # 停下后不再打开任何页面


def test_helpers():
    assert blocked("https://www.zhipin.com/web/geek/jobs?query=a", "正常列表") is None
    assert "安全验证" in blocked("https://www.zhipin.com/web/geek/jobs", "请完成安全验证")
    assert description_from_page(DETAIL).startswith("负责 Agent")
    assert "city=101010100" in search_url("Agent", "北京", None)
