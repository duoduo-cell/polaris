"""脉脉（B 档）：在采集专用的 Edge 里搜职言、按时间排序往下翻，读页面自己加载的列表。只采内容，不采人。

页面结构（2026-10-11 在用户登录的浏览器里看到的）：
- 搜索页 /web/search_center?type=gossip&query=… 加载时会先请求 /search/check_access_limit，
  返回 {is_limited, search_count_1h, search_count_24h}——脉脉自己给搜索设了上限。我们读它，说"已受限"就停。
- 点"时间排序"后页面请求 /search/gossips?…&limit=20&offset=0&sortby=time，往下滚依次加载 offset=20、40……
  每条职言里直接带全文（text）、发布时间（crtime）、点赞（likes）、评论数（total_cnt），不用逐条点开。
- 作者名、头像、职业等字段一律不存。
"""
import random
import re
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, ClassVar, Literal
from urllib.parse import urlencode

from sqlalchemy import select

from polaris.collectors.base import Query
from polaris.db.models import RawItem
from polaris.db.session import session_scope
from polaris.ingest import RawItemIn

MAIMAI = "https://maimai.cn"
BEIJING = timezone(timedelta(hours=8))
COLLECTOR_VERSION = "maimai-1"
KEEP = ("id", "egid", "crtime", "likes", "total_cnt", "summary")
TAG = re.compile(r"<[^>]+>")
BLOCK_TEXT = ("访问过于频繁", "操作频繁", "安全验证", "请完成验证", "账号异常", "登录后查看")


def search_url(keyword: str) -> str:
    return f"{MAIMAI}/web/search_center?{urlencode({'type': 'gossip', 'query': keyword, 'highlight': 'true'})}"


def detail_url(gid: str, egid: str) -> str:
    return f"{MAIMAI}/web/gossip_detail?{urlencode({'gid': gid, 'egid': egid})}"


def parse_time(text: str | None) -> datetime | None:
    try:
        return datetime.strptime(text or "", "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING)
    except ValueError:
        return None


def clean(text: str | None) -> str:
    return TAG.sub("", text or "").strip()


def to_item(g: dict[str, Any], keyword: str) -> RawItemIn | None:
    gid, egid = str(g.get("id") or ""), g.get("egid") or ""
    text = clean(g.get("text"))
    if not gid or not text:
        return None
    published = parse_time(g.get("crtime"))
    head = f"发布：{published:%Y-%m-%d}" if published else ""
    stats = f"点赞 {g.get('likes', '')}  评论 {g.get('total_cnt', '')}"
    return RawItemIn(source="maimai", tier="B", kind="post", url=detail_url(gid, egid), external_id=egid or gid,
                     query=keyword, raw_text="\n".join(x for x in (head, stats, "", text) if x is not None),
                     raw_json={k: g.get(k) for k in KEEP}, fetched_at=datetime.now(UTC), published_at=published,
                     fetch_method="browser", collector_version=COLLECTOR_VERSION)


def known_ids() -> set[str]:
    with session_scope() as s:
        return {r for r in s.execute(select(RawItem.external_id).where(RawItem.source == "maimai")).scalars() if r}


class MaimaiStats:
    def __init__(self) -> None:
        self.queries = self.searched = self.calls = self.failures = self.rate_limited = 0
        self.seen = self.skipped_known = self.too_old = 0
        self.seconds = 0.0
        self.stopped: str | None = None
        self.errors: list[str] = []
        self.search_count_24h: int | None = None


class MaimaiCollector:
    name: ClassVar[str] = "maimai"
    tier: ClassVar[Literal["A", "B"]] = "B"
    mode: ClassVar[Literal["search", "site"]] = "search"

    def __init__(self, context: Any, max_calls: int = 80, pause: tuple[float, float] = (6.0, 15.0),
                 since: datetime | None = None, max_pages: int = 10, known: set[str] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.context, self.max_calls, self.pause, self.max_pages, self.sleep = context, max_calls, pause, max_pages, sleep
        self.since = since or datetime.now(BEIJING) - timedelta(days=183)  # 近半年
        self.known = known if known is not None else known_ids()
        self.per_query_calls: int | None = None
        self.stats = MaimaiStats()
        self._query_start = 0
        self._page = None

    @property
    def page(self):
        if self._page is None:
            self._page = self.context.new_page()
        return self._page

    def _budget_left(self) -> bool:
        if self.stats.stopped or self.stats.calls >= self.max_calls:
            return False
        return self.per_query_calls is None or self.stats.calls - self._query_start < self.per_query_calls

    def _rest(self) -> None:
        self.sleep(random.uniform(*self.pause))

    def _blocked(self) -> bool:
        head = self.page.inner_text("body")[:2000]
        hit = next((w for w in BLOCK_TEXT if w in head), None)
        if hit or "login" in self.page.url:
            self.stats.stopped = f"疑似被拦：{'页面出现「' + hit + '」' if hit else '跳到了登录页'}"
            self.stats.rate_limited += 1
        return bool(self.stats.stopped)

    def _open_search(self, keyword: str) -> list[dict[str, Any]] | None:
        """打开职言搜索、读访问上限、点时间排序，返回第一页。"""
        self.stats.calls += 1
        start = time.perf_counter()
        try:
            with self.page.expect_response(lambda r: "/search/check_access_limit" in r.url, timeout=30000) as lim:
                self.page.goto(search_url(keyword), wait_until="domcontentloaded", timeout=45000)
            limit = lim.value.json()
            self.stats.search_count_24h = limit.get("search_count_24h")
            if limit.get("is_limited"):
                self.stats.stopped = "疑似被拦：脉脉的搜索访问上限说已受限"
                self.stats.rate_limited += 1
                return None
            self.sleep(random.uniform(2.0, 4.0))
            if self._blocked():
                return None
            with self.page.expect_response(lambda r: "/search/gossips" in r.url and r.status == 200, timeout=30000) as info:
                self.page.get_by_text("时间排序").first.click()
            return ((info.value.json().get("data") or {}).get("gossips")) or []
        except Exception as e:  # noqa: BLE001
            self.stats.failures += 1
            self.stats.errors.append(f"search {keyword}: {e}"[:200])
            return None
        finally:
            self.stats.seconds += time.perf_counter() - start

    def _next_page(self) -> list[dict[str, Any]] | None:
        """像人一样往下滚，让页面加载下一页。"""
        self.stats.calls += 1
        start = time.perf_counter()
        try:
            with self.page.expect_response(lambda r: "/search/gossips" in r.url and r.status == 200, timeout=20000) as info:
                for _ in range(3):
                    self.page.mouse.wheel(0, random.randint(1500, 2600))
                    self.sleep(random.uniform(0.6, 1.4))
            return ((info.value.json().get("data") or {}).get("gossips")) or []
        except Exception as e:  # noqa: BLE001  滚不出新的一页（到底了或超时）
            self.stats.errors.append(f"scroll: {e}"[:200])
            return None
        finally:
            self.stats.seconds += time.perf_counter() - start

    def collect(self, query: Query) -> Iterator[RawItemIn]:
        for keyword in query.keywords:
            if not self._budget_left():
                return
            self.stats.queries += 1
            self._query_start = self.stats.calls
            batch = self._open_search(keyword)
            if batch is None:
                if self.stats.stopped:
                    return
                continue
            self.stats.searched += 1
            for page_no in range(self.max_pages):
                old = 0
                for entry in batch:
                    g = (entry or {}).get("gossip") or {}
                    self.stats.seen += 1
                    published = parse_time(g.get("crtime"))
                    if published and published < self.since:
                        old += 1
                        self.stats.too_old += 1
                        continue
                    item = to_item(g, keyword)
                    if item is None:
                        continue
                    if item.external_id in self.known:
                        self.stats.skipped_known += 1
                        continue
                    self.known.add(item.external_id)
                    yield item
                # 按时间排序：这一页大半都早于半年前，后面只会更旧，换下一个词
                if not batch or old > len(batch) // 2 or page_no + 1 >= self.max_pages or not self._budget_left():
                    break
                self._rest()
                batch = self._next_page()
                if batch is None:
                    break
            self._rest()
