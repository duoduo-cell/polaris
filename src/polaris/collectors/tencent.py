"""腾讯招聘（A 档）：社招站有公开的查询和详情接口，不用签名、不用登录（2026-10-11 实测）。

列表接口给岗位名、事业群、地点、职责；详情接口再给任职要求。请求之间随机间隔，不给对方添负担。
"""
import random
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

import httpx

from polaris.collectors.base import Query
from polaris.ingest import RawItemIn

BASE = "https://careers.tencent.com/tencentcareer/api/post"
HEADERS = {"User-Agent": "Mozilla/5.0 (Polaris job-search agent; personal use)"}


def _ts() -> str:
    return str(int(time.time() * 1000))


def to_text(post: dict[str, Any]) -> str:
    parts = [post.get("RecruitPostName") or "", f"公司：腾讯（{post.get('BGName') or ''}）",
             f"地点：{post.get('LocationName') or ''}", f"类别：{post.get('CategoryName') or ''}"]
    if post.get("RequireWorkYearsName"):
        parts.append(f"经验要求：{post['RequireWorkYearsName']}")
    if post.get("LastUpdateTime"):
        parts.append(f"更新时间：{post['LastUpdateTime']}")
    parts += ["岗位职责：", (post.get("Responsibility") or "").strip()]
    if post.get("Requirement"):
        parts += ["任职要求：", post["Requirement"].strip()]
    return "\n".join(p for p in parts if p is not None)


class TencentCollector:
    name: ClassVar[str] = "tencent"
    tier: ClassVar[Literal["A", "B"]] = "A"
    mode: ClassVar[Literal["search", "site"]] = "search"

    def __init__(self, client: httpx.Client | None = None, page_size: int = 20, pause: tuple[float, float] = (0.6, 1.6),
                 with_detail: bool = True):
        self.client = client or httpx.Client(timeout=20, headers=HEADERS)
        self.page_size, self.pause, self.with_detail = page_size, pause, with_detail

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        r = self.client.get(f"{BASE}/{path}", params={"timestamp": _ts(), "language": "zh-cn", **params})
        r.raise_for_status()
        data = r.json()
        if data.get("Code") != 200:
            raise RuntimeError(f"腾讯接口返回 Code={data.get('Code')}")
        time.sleep(random.uniform(*self.pause))
        return data.get("Data") or {}

    def search(self, keyword: str, max_pages: int = 3) -> Iterator[dict[str, Any]]:
        for page in range(1, max_pages + 1):
            data = self._get("Query", {"keyword": keyword, "pageIndex": page, "pageSize": self.page_size, "area": "cn"})
            posts = data.get("Posts") or []
            yield from posts
            if len(posts) < self.page_size or page * self.page_size >= (data.get("Count") or 0):
                return

    def detail(self, post_id: str) -> dict[str, Any]:
        return self._get("ByPostId", {"postId": post_id})

    def collect(self, query: Query) -> Iterable[RawItemIn]:
        seen: set[str] = set()
        for keyword in query.keywords:
            for post in self.search(keyword, query.max_pages):
                pid = str(post.get("PostId"))
                if pid in seen:
                    continue
                seen.add(pid)
                full = {**post, **(self.detail(pid) if self.with_detail else {})}
                yield RawItemIn(source="tencent:careers", tier="A", kind="job", url=full.get("PostURL"),
                                external_id=pid, query=keyword, raw_text=to_text(full), raw_json=full,
                                fetched_at=datetime.now(UTC))
