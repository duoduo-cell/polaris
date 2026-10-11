"""小红书（B 档）的真实浏览器版：在采集专用的 Edge 里像人一样搜索、打开笔记，读页面自己的状态数据。

为什么从 Docker 里的无头浏览器换过来：MediaCrawler 的常见问题里写明小红书风控很严，强烈建议连接自己的真实浏览器，
不要用无头或标准 Playwright 模式；它的 issue #865 有人被永久封号（2026-10-11 查）。
做法照 xiaohongshu-mcp 的源码（Apache-2.0）移植：
- 搜索：打开 search_result 页，悬停"筛选"、按文字点选项（排序依据、发布时间），读 window.__INITIAL_STATE__.search.feeds；
- 笔记：打开 explore/{id} 页，读 __INITIAL_STATE__.note.noteDetailMap[id]，往下滚一点让第一页评论加载；
- 作者主页：打开 user/profile/{id} 页，读 __INITIAL_STATE__.user.notes。
返回的数据结构和 xiaohongshu-mcp 一致，所以 XhsCollector 的筛选、去重、遇阻即停逻辑原样复用（它的 caller 换成这里的 BrowserCaller）。
"""
import json
import random
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

XHS = "https://www.xiaohongshu.com"
DEFAULTS = {"排序依据": "综合", "发布时间": "不限"}
STATE_FEEDS = """() => { const f = window.__INITIAL_STATE__?.search?.feeds; const v = f && (f.value ?? f._value);
                          return v ? JSON.stringify(v) : ''; }"""
STATE_FEED_IDS = """() => { const f = window.__INITIAL_STATE__?.search?.feeds; const v = (f && (f.value ?? f._value)) || [];
                             return v.map(x => x.id).join(','); }"""
STATE_NOTE = """(id) => { const m = window.__INITIAL_STATE__?.note?.noteDetailMap; const v = m && (m.value ?? m._value ?? m);
                          return v && v[id] ? JSON.stringify(v[id]) : ''; }"""
STATE_USER_NOTES = """() => { const n = window.__INITIAL_STATE__?.user?.notes; const v = n && (n.value ?? n._value);
                              return v ? JSON.stringify(v.flat ? v.flat() : v) : ''; }"""
ERROR_SELECTORS = ".access-wrapper, .error-wrapper, .not-found-wrapper, .blocked-wrapper"


class BrowserCallFailed(RuntimeError):
    pass


def search_url(keyword: str) -> str:
    return f"{XHS}/search_result?{urlencode({'keyword': keyword, 'source': 'web_explore_feed'})}"


def note_url(note_id: str, token: str) -> str:
    return f"{XHS}/explore/{note_id}?{urlencode({'xsec_token': token, 'xsec_source': 'pc_feed'})}"


def profile_url(user_id: str, token: str) -> str:
    return f"{XHS}/user/profile/{user_id}?{urlencode({'xsec_token': token, 'xsec_source': 'pc_note'})}"


def page_problem(url: str, text: str) -> str | None:
    """被拦或掉登录的迹象：跳到验证或登录页、页面上出现验证或频繁字样。"""
    if any(w in url for w in ("captcha", "website-login", "/login")):
        return f"跳到了 {url[:80]}"
    head = text[:1500]
    for w in ("安全验证", "请完成验证", "访问频繁", "操作频繁", "账号异常", "登录后查看", "扫码登录"):
        if w in head:
            return f"页面出现「{w}」"
    return None


class BrowserCaller:
    """和 xiaohongshu-mcp 的工具同名、同样的返回结构：search_feeds、get_feed_detail、user_profile。"""

    def __init__(self, context: Any, sleep: Callable[[float], None] = time.sleep):
        self.context, self.sleep = context, sleep
        self._page = None

    @property
    def page(self):
        if self._page is None:
            self._page = self.context.new_page()
        return self._page

    def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = {"search_feeds": self.search, "get_feed_detail": self.detail, "user_profile": self.profile}.get(name)
        if handler is None:
            raise PermissionError(f"{name} 不是只读工具，北极星不调用它")
        return handler(args)

    def _goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        self.page.wait_for_function("() => window.__INITIAL_STATE__ !== undefined", timeout=20000)
        self.sleep(random.uniform(1.0, 2.5))  # 页面加载完先停一下，像人在看
        problem = page_problem(self.page.url, self.page.inner_text("body"))
        if problem:
            raise BrowserCallFailed(f"疑似被拦：{problem}")

    def _eval_json(self, expr: str, arg: Any = None) -> Any:
        text = self.page.evaluate(expr, arg) if arg is not None else self.page.evaluate(expr)
        return json.loads(text) if text else None

    def search(self, args: dict[str, Any]) -> dict[str, Any]:
        self._goto(search_url(args["keyword"]))
        filters = args.get("filters") or {}
        wanted = {"排序依据": filters.get("sort_by"), "发布时间": filters.get("publish_time")}
        pending = {k: v for k, v in wanted.items() if v and v != DEFAULTS[k]}
        if pending:
            self._apply_filters(pending)
        return {"feeds": self._eval_json(STATE_FEEDS) or []}

    def _apply_filters(self, pending: dict[str, str]) -> None:
        before = self.page.evaluate(STATE_FEED_IDS)
        for attempt in range(2):  # 悬停偶尔不出面板（10-11 见过两次），移开鼠标再悬停一次
            self.page.hover("div.filter")
            try:
                self.page.wait_for_selector("div.filter-panel", timeout=8000)
                break
            except Exception:
                if attempt:
                    raise
                self.page.mouse.move(10, 10)
                self.sleep(random.uniform(0.8, 1.5))
        for label, option in pending.items():
            group = self.page.locator("div.filter-panel div.filters").filter(
                has=self.page.locator(f":scope > span:text-is('{label}')"))
            group.locator("div.tags").filter(has_text=option).first.click(force=True)  # 悬浮层，跳过可点击检查
            self.sleep(random.uniform(0.4, 1.0))
        for _ in range(50):  # 等结果换成筛选后的。换的过程中列表会先变空，空的不算换好（10-11 踩过：读到空列表）
            ids = self.page.evaluate(STATE_FEED_IDS)
            if ids and ids != before:
                break
            self.sleep(0.3)

    def detail(self, args: dict[str, Any]) -> dict[str, Any]:
        note_id = args["feed_id"]
        self._goto(note_url(note_id, args["xsec_token"]))
        if self.page.locator(ERROR_SELECTORS).count():
            return {}
        try:  # 滚到评论区，让第一页评论加载出来
            self.page.locator(".comments-container").scroll_into_view_if_needed(timeout=3000)
            self.page.mouse.wheel(0, random.randint(300, 700))
            self.sleep(random.uniform(1.0, 2.0))
        except Exception:  # noqa: BLE001  没有评论区也没关系
            pass
        entry = self._eval_json(STATE_NOTE, note_id) or {}
        return {"data": {"note": entry.get("note") or {}, "comments": entry.get("comments") or {}}}

    def profile(self, args: dict[str, Any]) -> dict[str, Any]:
        self._goto(profile_url(args["user_id"], args["xsec_token"]))
        notes = self._eval_json(STATE_USER_NOTES) or []
        return {"data": {"feeds": [n for n in notes if isinstance(n, dict)]}}
