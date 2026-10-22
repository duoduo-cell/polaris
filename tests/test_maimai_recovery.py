from contextlib import contextmanager
from types import SimpleNamespace

from polaris.collectors.maimai import MaimaiCollector


class Page:
    url = "https://maimai.cn/web/search_center"

    def __init__(self, *, blocked=False, limited=False):
        self.blocked = blocked
        self.limited = limited
        self.reads = 0

    @contextmanager
    def expect_response(self, *args, **kwargs):
        self.reads += 1
        data = {"is_limited": self.limited} if self.reads == 1 else {"data": {"gossips": ["entry"]}}
        yield SimpleNamespace(value=SimpleNamespace(json=lambda: data))

    def goto(self, url, *, wait_until, timeout):
        assert wait_until == "commit"  # 必须先读接口响应，不能依赖整页加载
        if self.blocked:
            self.url = "https://maimai.cn/login"
            raise TimeoutError("navigation timed out")

    def inner_text(self, *args):
        return "登录后查看" if self.blocked else "搜索结果"

    def get_by_text(self, *args):
        return SimpleNamespace(first=SimpleNamespace(click=lambda: None))


def collector(page):
    return MaimaiCollector(SimpleNamespace(new_page=lambda: page), known=set(), sleep=lambda _: None)


def test_search_reads_limit_before_sort_response():
    c = collector(Page())
    assert c._open_search("Agent") == ["entry"]
    assert c.stats.calls == 1 and c.stats.failures == 0


def test_timeout_at_login_is_a_stop_not_an_empty_search():
    c = collector(Page(blocked=True))
    assert c._open_search("Agent") is None
    assert c.stats.failures == 1 and c.stats.stopped


def test_platform_limit_stops_before_sorting():
    page = Page(limited=True)
    c = collector(page)
    assert c._open_search("Agent") is None
    assert c.stats.stopped and page.reads == 1
