"""需要浏览器的大厂官网（A 档）：字节跳动、小红书。用 Playwright 驱动系统自带的 Edge，不另下载浏览器。

- 字节：列表接口带页面自己算的签名，不能直接调；用浏览器正常加载列表页，读取页面拿到的接口返回，
  里面已经有每个岗位完整的职责和要求（2026-10-11 实测）。
- 小红书招聘官网：列表页拿到岗位链接，再逐个打开详情页读"工作职责"和"任职资格"（正文晚于标题渲染，要等）。
"""
import random
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal
from urllib.parse import urlencode

from polaris.collectors.base import Query
from polaris.ingest import RawItemIn

BYTEDANCE_LIST = "https://jobs.bytedance.com/campus/position"
BYTEDANCE_RD = "6704215862603155720"  # 职位类别：研发
BYTEDANCE_DAILY_INTERN = "7194661644654577981"  # 招聘项目：日常实习
XHS_JOBS = "https://job.xiaohongshu.com"


@contextmanager
def browser(headless: bool = True):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=headless)
        try:
            yield b
        finally:
            b.close()


def bytedance_text(job: dict[str, Any]) -> str:
    cities = "、".join(c.get("name", "") for c in (job.get("city_list") or [])) or (job.get("city_info") or {}).get("name", "")
    category = (job.get("job_category") or {}).get("name", "")
    recruit = (job.get("recruit_type") or {}).get("name", "")
    subject = ((job.get("job_subject") or {}).get("name") or {}).get("zh_cn", "")
    return "\n".join([job.get("title") or "", "公司：字节跳动", f"地点：{cities}", f"类型：{recruit} {subject}".strip(),
                      f"类别：{category}", "职位描述：", (job.get("description") or "").strip(),
                      "职位要求：", (job.get("requirement") or "").strip()])


class ByteDanceCollector:
    name: ClassVar[str] = "bytedance"
    tier: ClassVar[Literal["A", "B"]] = "A"
    mode: ClassVar[Literal["search", "site"]] = "search"

    def __init__(self, page_size: int = 50, pause: tuple[float, float] = (2.0, 5.0), intern_only: bool = True):
        self.page_size, self.pause, self.intern_only = page_size, pause, intern_only

    def list_url(self, keyword: str, page: int) -> str:
        params = {"keywords": keyword, "category": BYTEDANCE_RD, "current": page, "limit": self.page_size}
        if self.intern_only:
            params["project"] = BYTEDANCE_DAILY_INTERN
        return f"{BYTEDANCE_LIST}?{urlencode(params)}"

    def collect(self, query: Query) -> Iterable[RawItemIn]:
        seen: set[str] = set()
        with browser() as b:
            page = b.new_page()
            for keyword in query.keywords:
                for n in range(1, query.max_pages + 1):
                    with page.expect_response(lambda r: "/api/v1/search/job/posts" in r.url and r.request.method == "POST"
                                              and r.status == 200, timeout=40000) as info:
                        page.goto(self.list_url(keyword, n), wait_until="commit")  # 只等接口返回，不等埋点加载完
                    data = info.value.json().get("data") or {}
                    jobs = data.get("job_post_list") or []
                    for job in jobs:
                        jid = str(job.get("id"))
                        if jid in seen:
                            continue
                        seen.add(jid)
                        yield RawItemIn(source="bytedance:campus", tier="A", kind="job",
                                        url=f"{BYTEDANCE_LIST}/{jid}/detail", external_id=jid, query=keyword,
                                        raw_text=bytedance_text(job), raw_json=job, fetched_at=datetime.now(UTC),
                                        fetch_method="browser", collector_version="bytedance-1",
                                        published_at=datetime.fromtimestamp(int(job["publish_time"]) / 1000, UTC)
                                        if str(job.get("publish_time") or "").isdigit() else None)
                    time.sleep(random.uniform(*self.pause))
                    if len(jobs) < self.page_size or n * self.page_size >= (data.get("count") or 0):
                        break


class XhsCareersCollector:
    """小红书招聘官网（2027 届校招和实习，研发方向）。"""

    name: ClassVar[str] = "xhs_careers"
    tier: ClassVar[Literal["A", "B"]] = "A"
    mode: ClassVar[Literal["search", "site"]] = "search"

    def __init__(self, pause: tuple[float, float] = (1.0, 2.5), max_jobs: int = 60):
        self.pause, self.max_jobs = pause, max_jobs

    @staticmethod
    def _navigate(page, url: str) -> None:
        from playwright.sync_api import TimeoutError as NavigationTimeout

        # 页面响应后，下面仍逐项等岗位链接或任职资格；不让无关资源的加载挡住正文。
        for attempt in range(3):
            try:
                response = page.goto(url, wait_until="commit", timeout=45000)
            except NavigationTimeout:
                if attempt == 2:
                    raise
                time.sleep(2.0 ** (attempt + 1))
                continue
            if response is not None and response.status >= 400:
                raise RuntimeError(f"小红书招聘官网返回 HTTP {response.status}，停止本轮")
            return

    def _links(self, page, keyword: str, max_pages: int) -> list[tuple[str, str]]:
        self._navigate(page, f"{XHS_JOBS}/campus/position?{urlencode({'keyword': keyword, 'direction': 'rd'})}")
        page.wait_for_selector('a[href^="/campus/position/"]', timeout=30000)
        found: dict[str, str] = {}
        for _ in range(max_pages):
            for a in page.query_selector_all('a[href^="/campus/position/"]'):
                href = a.get_attribute("href") or ""
                if href.rstrip("/").split("/")[-1].isdigit() and href not in found:
                    found[href] = (a.inner_text() or "").split("\n")[0]
            nxt = page.query_selector(".ant-pagination-next:not(.ant-pagination-disabled)")
            if not nxt or len(found) >= self.max_jobs:
                break
            before = len(found)
            nxt.click()
            page.wait_for_timeout(1500)
            if len({a.get_attribute("href") for a in page.query_selector_all('a[href^="/campus/position/"]')} - set(found)) == 0 \
                    and len(found) == before:
                break
        return list(found.items())[: self.max_jobs]

    def _detail(self, page, href: str) -> str | None:
        self._navigate(page, XHS_JOBS + href)
        # 页面先渲染标题和占位的"-"，正文后到；要等"任职资格"到页脚之间真的有内容
        ready = """() => {
            const t = document.body.innerText, m = t.match(/任职资格|任职要求|职位要求/);
            if (!m) return false;
            const after = t.slice(m.index + m[0].length), end = after.indexOf('社区精选');
            const sec = (end > 0 ? after.slice(0, end) : after).trim();
            return sec.length > 40 && !sec.startsWith('-');
        }"""
        try:
            page.wait_for_function(ready, timeout=20000)
        except Exception:  # noqa: BLE001
            return None
        text = page.inner_text("body")
        start, end = text.find("【"), text.find("社区精选")
        return text[max(start, 0): end if end > 0 else None].strip()

    def collect(self, query: Query) -> Iterator[RawItemIn]:
        seen: set[str] = set()
        with browser() as b:
            page = b.new_page()
            for keyword in query.keywords:
                for href, title in self._links(page, keyword, query.max_pages):
                    if href in seen:
                        continue
                    seen.add(href)
                    text = self._detail(page, href)
                    time.sleep(random.uniform(*self.pause))
                    if not text:
                        continue
                    jid = href.rstrip("/").split("/")[-1]
                    yield RawItemIn(source="xhs:careers", tier="A", kind="job", url=XHS_JOBS + href, external_id=jid,
                                    query=keyword, raw_text=f"{text}\n公司：小红书", raw_json={"title": title, "href": href},
                                    fetched_at=datetime.now(UTC), fetch_method="browser",
                                    collector_version="xhs-careers-1")
