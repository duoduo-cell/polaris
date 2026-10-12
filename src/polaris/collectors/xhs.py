"""小红书（B 档）：通过本机运行的 xiaohongshu-mcp 服务读取。只放行只读工具，评论、点赞、收藏、发布一律屏蔽。

服务在你自己的电脑上跑（docker compose --profile local up -d xhs-mcp），用你本人的账号扫码登录。
每次调用约 48 秒（工具真的在开浏览器），所以采集放在后台慢慢跑，每轮有调用次数上限。
"""
import asyncio
import base64
import json
import os
import random
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from sqlalchemy import select

from polaris.collectors.base import Query
from polaris.companies import is_big
from polaris.config import ROOT
from polaris.db.models import RawItem
from polaris.db.session import session_scope
from polaris.ingest import RawItemIn

XHS_URL = os.environ.get("XHS_MCP_URL", "http://127.0.0.1:18060/mcp")
# 白名单：只读。发笔记、评论、点赞、收藏、回复通知等写操作，以及读你自己主页和通知的工具，都不在里面
READ_ONLY_TOOLS = frozenset({"check_login_status", "get_login_qrcode",
                             "search_feeds", "get_feed_detail", "user_profile", "list_feeds"})
VERTICAL_WORDS = ("实习", "内推", "面经", "招聘", "hc", "HC", "offer", "日常实习", "暑期", "校招", "转正", "base")
RECRUITER_WORDS = ("内推", "招聘", "招人", "hc", "HC", "直招", "收简历")
# 出现这些字样说明平台在拦：验证码、操作频繁、登录失效、账号异常。碰到一次就停下当天的采集
BLOCK_WORDS = ("验证", "频繁", "安全限制", "账号异常", "未登录", "登录失效", "请登录", "登录后查看", "扫码登录",
               "captcha", "too many", "风控", "疑似被拦")
PAYLOAD_KEYS = ("feeds", "data", "note", "notes", "userBasicInfo")
MAX_CONSECUTIVE_FAILURES = 3
COLLECTOR_VERSION = "xhs-2"  # 2：半年内、多种排序、遇阻即停
MAX_CONSECUTIVE_EMPTY = 5


class BlockedTool(PermissionError):
    pass


class XhsCallFailed(RuntimeError):
    pass


def _token() -> str | None:
    if os.environ.get("XHS_MCP_TOKEN"):
        return os.environ["XHS_MCP_TOKEN"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("XHS_MCP_TOKEN="):
                return line.split("=", 1)[1].strip() or None
    return None


def _headers() -> dict[str, str] | None:
    token = _token()
    return {"Authorization": f"Bearer {token}"} if token else None


async def _call(name: str, args: dict[str, Any] | None = None) -> list[Any]:
    if name not in READ_ONLY_TOOLS:
        raise BlockedTool(f"{name} 不是只读工具，北极星不调用它")
    async with streamablehttp_client(XHS_URL, headers=_headers()) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, args or {})
            return list(result.content)


def call(name: str, args: dict[str, Any] | None = None) -> list[Any]:
    return asyncio.run(_call(name, args))


async def list_tools() -> list[dict[str, Any]]:
    async with streamablehttp_client(XHS_URL, headers=_headers()) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return [{"name": t.name, "schema": t.inputSchema} for t in tools.tools]


def text_of(content: list[Any]) -> str:
    return "\n".join(getattr(c, "text", "") for c in content if getattr(c, "type", "") == "text")


def call_json(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """调一个只读工具并解析 JSON；工具内部出错（比如页面加载超时）时抛 XhsCallFailed。"""
    text = text_of(call(name, args))
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise XhsCallFailed(text[:200]) from e


def login_qrcode() -> tuple[bytes | None, str]:
    """返回 (二维码 PNG, 文字说明)。已经登录时没有二维码。"""
    content = call("get_login_qrcode")
    png = None
    for c in content:
        if getattr(c, "type", "") == "image":
            png = base64.b64decode(c.data)
    text = text_of(content)
    if png is None and "base64," in text:  # 有的版本把图片放在文字里
        png = base64.b64decode(text.split("base64,", 1)[1].split('"')[0].split()[0])
    return png, text


def login_status() -> str:
    return text_of(call("check_login_status"))


# ---------------- 采集：横向搜、纵向挖 ----------------

def note_url(note_id: str, xsec_token: str | None) -> str:
    return f"https://www.xiaohongshu.com/explore/{note_id}" + (f"?xsec_token={xsec_token}" if xsec_token else "")


def worth_opening(feed: dict[str, Any]) -> bool:
    """纵向挖的规则：标题里提到大厂，或者提到实习、内推、面经、招聘这类词，才打开看正文和评论。"""
    card = feed.get("noteCard") or {}
    title = card.get("displayTitle") or ""
    return feed.get("modelType", "note") == "note" and (is_big(title) or any(w in title for w in VERTICAL_WORDS))


def slim_comments(comments: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """评论只留内容、点赞数、时间和属地，不留评论者的昵称和编号。"""
    return [{"content": c.get("content"), "likes": c.get("likeCount"), "time": c.get("createTime"),
             "ip": c.get("ipLocation")} for c in comments[:limit]]


def to_text(note: dict[str, Any], comments: list[dict[str, Any]]) -> str:
    when = datetime.fromtimestamp(note["time"] / 1000, UTC).strftime("%Y-%m-%d") if note.get("time") else ""
    stats = note.get("interactInfo") or {}
    author = (note.get("user") or {}).get("nickname") or ""
    lines = [note.get("title") or "",
             f"发布：{when}  属地：{note.get('ipLocation') or ''}  作者：{author}",
             f"点赞 {stats.get('likedCount', '')}  收藏 {stats.get('collectedCount', '')}  评论 {stats.get('commentCount', '')}",
             "", (note.get("desc") or "").strip()]
    if comments:
        lines += ["", "评论："] + [f"- {c['content']}" for c in comments if c.get("content")]
    return "\n".join(lines)


def known_note_ids() -> set[str]:
    with session_scope() as s:
        rows = s.execute(select(RawItem.external_id).where(RawItem.source == "xhs")).scalars().all()
    return {r for r in rows if r}


@dataclass
class XhsStats:
    queries: int = 0
    searched: int = 0
    feeds_seen: int = 0
    skipped_known: int = 0
    skipped_rule: int = 0
    opened: int = 0
    profiles: int = 0
    failures: int = 0
    calls: int = 0
    seconds: float = 0.0
    stopped: str | None = None
    errors: list[str] = field(default_factory=list)


class XhsCollector:
    """小红书采集器（B 档，本机跑）。

    横向：每个查询词搜一周内最新的笔记，按笔记编号去重，采过的跳过。
    纵向：标题提到大厂或实习、内推、面经、招聘的才打开正文和评论；发内推、招聘帖的作者再看主页。
    每次调用之间随机停几秒，一轮有调用次数上限。
    """

    name: ClassVar[str] = "xhs"
    tier: ClassVar[Literal["A", "B"]] = "B"
    mode: ClassVar[Literal["search", "site"]] = "search"

    def __init__(self, max_calls: int = 30, pause: tuple[float, float] = (3.0, 8.0), publish_time: str = "一周内",
                 open_profiles: bool = True, known: set[str] | None = None,
                 caller: Callable[[str, dict[str, Any]], dict[str, Any]] = call_json,
                 sorts: tuple[str, ...] = ("最新",)):
        self.max_calls, self.pause, self.publish_time, self.sorts = max_calls, pause, publish_time, sorts
        self.open_profiles = open_profiles
        self.known = known if known is not None else known_note_ids()
        self.caller = caller
        self.per_query_calls: int | None = None  # 每个查询词最多用几次调用，免得第一个词把预算用光
        self.stats = XhsStats()
        self._query_start = 0
        self._fails_in_a_row = 0
        self._empty_in_a_row = 0
        self._seen_feeds: set[str] = set()  # 本次已经看过（打开或按规则跳过）的，换个排序再搜到不重复判断

    def _stop(self, reason: str) -> None:
        if not self.stats.stopped:
            self.stats.stopped = reason

    def _call(self, name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        if self.stats.stopped or self.stats.calls >= self.max_calls:
            return None
        if self.per_query_calls is not None and self.stats.calls - self._query_start >= self.per_query_calls:
            return None
        self.stats.calls += 1
        start = time.perf_counter()
        try:
            result = self.caller(name, args)
            self._fails_in_a_row = 0
            # 只在没有正常内容时才看拦截字样：笔记正文里出现"验证""频繁"不算
            has_payload = isinstance(result, dict) and any(result.get(k) for k in PAYLOAD_KEYS)
            if not has_payload and any(w in str(result)[:2000] for w in BLOCK_WORDS):
                self._stop(f"疑似被拦：{name} 返回 {str(result)[:120]}")
            return result
        except Exception as e:  # noqa: BLE001  单次失败不影响整轮
            self.stats.failures += 1
            self._fails_in_a_row += 1
            msg = f"{name}: {e}"[:200]
            self.stats.errors.append(msg)
            if any(w in str(e) for w in BLOCK_WORDS):
                self._stop(f"疑似被拦：{msg}")
            elif self._fails_in_a_row >= MAX_CONSECUTIVE_FAILURES:
                self._stop(f"连续失败 {self._fails_in_a_row} 次：{msg}")
            return None
        finally:
            self.stats.seconds += time.perf_counter() - start
            if self.pause[1] > 0:
                time.sleep(random.uniform(*self.pause))

    def _open(self, feed_id: str, token: str, query: str) -> RawItemIn | None:
        detail = self._call("get_feed_detail", {"feed_id": feed_id, "xsec_token": token})
        if not detail:
            return None
        detail = detail.get("data", detail)
        note = detail.get("note") or {}
        if not note:
            return None
        self.stats.opened += 1
        self.known.add(feed_id)
        if hasattr(self.caller, "page"):  # 采集浏览器：同一次浏览里把评论翻完、图片读成字（10-12，见 xhs_deep）
            from polaris.collectors.xhs_deep import VERSION, enrich

            text, raw_json, _ = enrich(self.caller, feed_id, note, detail.get("comments") or {})
            version = VERSION
        else:
            comments = slim_comments((detail.get("comments") or {}).get("list") or [])
            kept = {k: note.get(k) for k in ("noteId", "title", "desc", "type", "time", "ipLocation", "interactInfo")}
            kept["user"] = {k: (note.get("user") or {}).get(k) for k in ("userId", "nickname")}
            text, raw_json, version = to_text(note, comments), {"note": kept, "comments": comments}, COLLECTOR_VERSION
        return RawItemIn(source="xhs", tier="B", kind="post", url=note_url(feed_id, token), external_id=feed_id,
                         query=query, raw_text=text, raw_json=raw_json,
                         fetched_at=datetime.now(UTC), fetch_method="mcp", collector_version=version,
                         published_at=datetime.fromtimestamp(note["time"] / 1000, UTC) if note.get("time") else None)

    def collect(self, query: Query) -> Iterator[RawItemIn]:
        for keyword in query.keywords:
            if self.stats.stopped or self.stats.calls >= self.max_calls:
                return
            self.stats.queries += 1
            self._query_start = self.stats.calls
            for sort in self.sorts:  # 同一个词换几种排序搜：最新的、最多点赞的、最多收藏的，覆盖更全
                result = self._call("search_feeds", {"keyword": keyword,
                                                     "filters": {"sort_by": sort, "publish_time": self.publish_time}})
                if not result:
                    continue
                self.stats.searched += 1
                feeds = result.get("feeds") or []
                self._empty_in_a_row = 0 if feeds else self._empty_in_a_row + 1
                if self._empty_in_a_row >= MAX_CONSECUTIVE_EMPTY:
                    self._stop(f"连续 {self._empty_in_a_row} 次搜索一条结果都没有，可能被限制")
                    return
                for feed in feeds:
                    self.stats.feeds_seen += 1
                    fid, token = feed.get("id"), feed.get("xsecToken")
                    if not fid or not token or fid in self._seen_feeds:
                        continue
                    self._seen_feeds.add(fid)
                    if fid in self.known:
                        self.stats.skipped_known += 1
                        continue
                    if not worth_opening(feed):
                        self.stats.skipped_rule += 1
                        continue
                    item = self._open(fid, token, keyword)
                    if item is None:
                        continue
                    yield item
                    yield from self._author_posts(feed, keyword)

    def _author_posts(self, feed: dict[str, Any], keyword: str) -> Iterator[RawItemIn]:
        """作者发的是内推或招聘帖时，再看他主页上的其他同类笔记（纵向再深一层）。"""
        card = feed.get("noteCard") or {}
        title, user = card.get("displayTitle") or "", card.get("user") or {}
        if not self.open_profiles or not any(w in title for w in RECRUITER_WORDS):
            return
        uid, token = user.get("userId"), user.get("xsecToken") or feed.get("xsecToken")
        if not uid or not token:
            return
        profile = self._call("user_profile", {"user_id": uid, "xsec_token": token})
        if not profile:
            return
        self.stats.profiles += 1
        data = profile.get("data", profile)
        for other in (data.get("feeds") or data.get("notes") or [])[:5]:
            oid, otoken = other.get("id"), other.get("xsecToken")
            if oid and otoken and oid not in self.known and worth_opening(other):
                item = self._open(oid, otoken, f"{keyword}（作者主页）")
                if item is not None:
                    yield item
