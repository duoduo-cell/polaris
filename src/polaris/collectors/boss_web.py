"""Boss直聘（B 档）：在采集专用的 Edge 里像人一样浏览，读页面自己加载的岗位列表。

和 boss-cli 版的区别：不从 Python 直接调 Boss 的接口，也不需要自己生成任何令牌——
搜索页由浏览器正常打开，Boss 自己的脚本去请求 /wapi/zpgeek/search/joblist.json，我们只读这个返回；
详情页也是正常打开、读页面上的职位描述。见 browser.py 和 DATASET.md 第 4 节。

只做：打开搜索页、看列表、打开大厂岗位的详情。不打招呼、不投递、不聊天。
"""
import random
import re
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal
from urllib.parse import urlencode

from polaris.collectors.base import Query
from polaris.collectors.boss import BossStats, known_job_ids, slim_card, to_text
from polaris.companies import match
from polaris.ingest import RawItemIn

BOSS = "https://www.zhipin.com"
SEARCH_API = "/wapi/zpgeek/search/joblist.json"
CITY_CODES = {"全国": "100010000", "北京": "101010100", "上海": "101020100", "广州": "101280100",
              "深圳": "101280600", "杭州": "101210100", "成都": "101270100"}
SCALE_CODES = {"1000-9999人": "305", "10000人以上": "306"}
# 页面被拦时常见的字样或跳转（安全验证、登录、访问异常）
BLOCK_URL = re.compile(r"verify|security|safe|captcha|/web/user/\?|passport", re.I)
BLOCK_TEXT = ("安全验证", "访问异常", "异常访问", "请完成验证", "当前IP", "操作频繁", "请登录")
COLLECTOR_VERSION = "boss-web-1"


def search_url(keyword: str, city: str, scale: str | None) -> str:
    params = {"query": keyword, "city": CITY_CODES.get(city, city)}
    if scale:
        params["scale"] = SCALE_CODES.get(scale, scale)
    return f"{BOSS}/web/geek/jobs?{urlencode(params)}"


def detail_url(encrypt_job_id: str) -> str:
    return f"{BOSS}/job_detail/{encrypt_job_id}.html"


def blocked(url: str, text: str) -> str | None:
    if BLOCK_URL.search(url):
        return f"跳到了 {url[:80]}"
    hit = next((w for w in BLOCK_TEXT if w in text[:3000]), None)
    return f"页面出现「{hit}」" if hit else None


def description_from_page(text: str) -> str:
    """详情页正文里，从"职位描述"到"公司介绍"或"工商信息"之间那段。"""
    start = text.find("职位描述")
    if start < 0:
        return ""
    end = min([i for i in (text.find(w, start) for w in ("公司介绍", "工商信息", "BOSS 安全提示", "竞争力分析")) if i > 0]
              or [len(text)])
    return text[start + len("职位描述"):end].strip()


class BossWebCollector:
    name: ClassVar[str] = "boss"
    tier: ClassVar[Literal["A", "B"]] = "B"
    mode: ClassVar[Literal["search", "site"]] = "search"

    def __init__(self, context: Any, cities: tuple[str, ...] = ("北京", "上海", "深圳", "杭州"),
                 scales: tuple[str | None, ...] = ("10000人以上", "1000-9999人"), max_calls: int = 150,
                 pause: tuple[float, float] = (4.0, 9.0), known: set[str] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.context, self.cities, self.scales = context, cities, scales
        self.max_calls, self.pause, self.sleep = max_calls, pause, sleep
        self.known = known if known is not None else known_job_ids()
        self.per_query_calls: int | None = None
        self.stats = BossStats()
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

    def _check(self) -> bool:
        reason = blocked(self.page.url, self.page.inner_text("body") if self.page.url.startswith(BOSS) else "")
        if reason:
            self.stats.stopped = f"疑似被拦：{reason}"
            self.stats.rate_limited += 1
        return reason is None

    def _search(self, keyword: str, city: str, scale: str | None) -> list[dict[str, Any]] | None:
        if not self._budget_left():
            return None
        self.stats.calls += 1
        start = time.perf_counter()
        try:
            with self.page.expect_response(lambda r: SEARCH_API in r.url and r.status == 200, timeout=30000) as info:
                self.page.goto(search_url(keyword, city, scale), wait_until="domcontentloaded")
            data = info.value.json()
        except Exception as e:  # noqa: BLE001  没等到列表接口：先看是不是被拦了
            self.stats.failures += 1
            self.stats.errors.append(f"search {keyword}/{city}: {e}"[:200])
            self._check()
            return None
        finally:
            self.stats.seconds += time.perf_counter() - start
        if not self._check():
            return None
        if data.get("code") not in (0, None):  # Boss 接口用 code 表示异常，比如要验证
            self.stats.stopped = f"疑似被拦：列表接口返回 code={data.get('code')} {str(data.get('message'))[:60]}"
            self.stats.rate_limited += 1
            return None
        return (data.get("zpData") or {}).get("jobList") or []

    def _detail(self, encrypt_job_id: str) -> str | None:
        if not self._budget_left():
            return None
        self.stats.calls += 1
        start = time.perf_counter()
        try:
            self.page.goto(detail_url(encrypt_job_id), wait_until="domcontentloaded")
            self.page.wait_for_timeout(1500)
            if not self._check():
                return None
            return description_from_page(self.page.inner_text("body")) or None
        except Exception as e:  # noqa: BLE001
            self.stats.failures += 1
            self.stats.errors.append(f"detail {encrypt_job_id}: {e}"[:200])
            return None
        finally:
            self.stats.seconds += time.perf_counter() - start

    def collect(self, query: Query) -> Iterator[RawItemIn]:
        for keyword in query.keywords:
            if self.stats.stopped or self.stats.calls >= self.max_calls:
                return
            self.stats.queries += 1
            self._query_start = self.stats.calls
            searched = False
            for city in self.cities:
                for scale in self.scales:
                    jobs = self._search(keyword, city, scale)
                    self._rest()
                    if jobs is None:
                        if self.stats.stopped:
                            return
                        continue
                    searched = True
                    yield from self._open_big(jobs, keyword)
            self.stats.searched += searched

    def _open_big(self, jobs: list[dict[str, Any]], keyword: str) -> Iterator[RawItemIn]:
        for job in jobs:
            self.stats.cards_seen += 1
            big = match(job.get("brandName"))
            if not big:
                self.stats.skipped_small += 1
                continue
            jid = job.get("encryptJobId")
            if not jid:
                continue
            if jid in self.known:
                self.stats.skipped_known += 1
                continue
            text = self._detail(jid)
            self._rest()
            if text is None:
                if self.stats.stopped:
                    return
                continue
            self.stats.opened += 1
            self.known.add(jid)
            card = slim_card(job)
            yield RawItemIn(source="boss", tier="B", kind="job", url=detail_url(jid), external_id=jid, query=keyword,
                            raw_text=to_text(card, {"jobInfo": {"postDescription": text}}),
                            raw_json={"card": card, "big_company": big.name}, fetched_at=datetime.now(UTC),
                            fetch_method="browser", collector_version=COLLECTOR_VERSION)
