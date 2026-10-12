"""现查：对话里按需去原链接看最新的（纵向），或去平台现场搜（横向）。主 agent 的 source_open、platform_search 用它。

规矩和常驻采集一样（DATASET.md 第 4 节，10-12 用户确认）：
- 官网岗位（字节、腾讯、小红书招聘）是公开页面，不登录；小红书、脉脉用采集专用浏览器（你本人登录、只读），Boss 用 boss-cli。
- 不伪装、不换号、不用代理、不绕验证码；页面出现验证或限流字样就停，告诉你。
- 每次回答最多现查 3 次（在 ToolRuntime 里数）；要登录的平台和常驻采集共用每天的额度和每小时的上限（记成一条 live 的采集 run），
  同一个平台同一时间只有一个任务在用（collectors/locks.py）。浏览器没开或掉了登录，直接说明，不硬来。
- 查到的写回原始层（raw_items），常驻采集下一轮会抽取、对齐；回答里用 R- 编号引用，聊天里能点开。
"""
import difflib
import random
import time
from datetime import UTC, datetime
from itertools import islice
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from polaris.collectors.locks import PlatformBusy, platform_lock
from polaris.db.models import Job, Post, Posting, RawItem
from polaris.db.session import session_scope
from polaris.ingest import RawItemIn, content_hash, ingest_raw_items
from polaris.sources import label, source_dict

LOGIN_SOURCES = ("xhs", "maimai", "boss")
PAUSE = {"xhs": (4.0, 8.0), "maimai": (3.0, 6.0), "boss": (2.0, 4.0)}  # 现查只开几页，停顿比常驻采集短，但仍随机
GONE_WORDS = ("职位已下线", "职位已关闭", "职位不存在", "该职位已", "已停止招聘", "页面不存在", "笔记不见了", "内容已被删除",
              "当前内容无法展示")
BLOCK_WORDS = ("验证码", "安全验证", "访问频繁", "操作频繁", "请完成验证", "账号异常")


class LiveUnavailable(RuntimeError):
    """现查做不了：浏览器没开、没登录、额度用完、平台忙、被拦。消息直接给模型和用户看。"""


def _budget_ok(source: str) -> None:
    if source not in LOGIN_SOURCES:
        return
    from polaris.collectors import budget

    cap, why = budget.today_cap(source)
    if cap <= 0:
        raise LiveUnavailable(f"{label(source)}今天不能再查：{why}")
    if budget.used_today(source) >= cap:
        raise LiveUnavailable(f"{label(source)}今天的量（{cap} 次）已经用完，明天再查")
    if budget.hour_left(source) <= 0:
        raise LiveUnavailable(f"{label(source)}这一小时的量用完了，过一会儿再查（按速度限，免得被平台限制）")


def _context(source: str):
    """接上采集专用浏览器（带你的登录）。没开就不替你打开——现查不该在你不知道时弹窗。"""
    from polaris.browser import attached, is_running

    if not is_running("collect"):
        raise LiveUnavailable("采集专用浏览器没开，现在查不了小红书和脉脉。运行 polaris browser open 打开并登录后再试")
    return attached()


def _check_login(context: Any, source: str) -> None:
    from polaris.logins import logged_in

    if not logged_in(context, source):
        raise LiveUnavailable(f"{label(source)}没登录，在采集窗口里扫一次码就能查")


def _record(source: str, query: str | None, calls: int, stopped: str | None, items: int) -> None:
    """记成一条采集 run，常驻采集算每天、每小时的量时会把现查也算进去。"""
    from polaris.runs import run_scope

    with run_scope("collect", {"source": source, "live": True, "queries": [query] if query else [], "max_calls": calls}) as (_, stats):
        stats.update({"calls": calls, "items": items, **({"stopped": stopped} if stopped else {})})


def _store(items: list[RawItemIn]) -> dict[tuple[str, str], int]:
    """写进原始层，返回（来源，内容指纹）→ 原始条目编号（已经有的也给编号）。"""
    if not items:
        return {}
    ingest_raw_items(items)
    keys = {(i.source, content_hash(i.raw_text)) for i in items}
    with session_scope() as s:
        rows = s.execute(select(RawItem.id, RawItem.source, RawItem.content_hash)
                         .where(RawItem.content_hash.in_([k[1] for k in keys]))).all()
    return {(src, h): rid for rid, src, h in rows if (src, h) in keys}


def _row(item: RawItemIn, rid: int | None, text_limit: int = 1200) -> dict[str, Any]:
    from polaris.collectors.xhs_deep import brief
    from polaris.tools.base import untrusted

    first = item.raw_text.strip().splitlines()[0][:80] if item.raw_text.strip() else ""
    return {"id": f"R-{rid}" if rid else None, "source": label(item.source), "title": first,
            "published": item.published_at.date().isoformat() if item.published_at else None, "url": item.url,
            "text": untrusted(brief(item.raw_text, 2400) if item.source == "xhs" else item.raw_text[:text_limit])}


# ---------- 横向：现场搜 ----------

def search(platform: str, query: str, limit: int = 5, city: str | None = None) -> dict[str, Any]:
    if platform not in ("xhs", "maimai", "boss", "tencent"):
        raise LiveUnavailable(f"不支持现查 {platform}")
    _budget_ok(platform)
    try:
        with platform_lock(platform, timeout=40):
            if platform == "tencent":
                items, calls, stopped, listed = _search_tencent(query, limit)
            elif platform == "boss":
                items, calls, stopped, listed = _search_boss(query, limit, city)
            else:
                with _context(platform) as context:
                    _check_login(context, platform)
                    items, calls, stopped, listed = (_search_xhs if platform == "xhs" else _search_maimai)(context, query, limit)
    except PlatformBusy as e:
        raise LiveUnavailable(f"{e}，等它这一轮结束再查") from e
    if platform in LOGIN_SOURCES:
        _record(platform, query, calls, stopped, len(items))
    ids = _store(items)
    out: dict[str, Any] = {
        "platform": label(platform), "query": query, "searched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scope": "刚刚在平台上现搜的结果（不是本地库）", "returned": len(items),
        "items": [_row(i, ids.get((i.source, content_hash(i.raw_text)))) for i in items]}
    if listed:
        out["also_listed"] = listed  # 搜到但没打开正文的，只有标题和链接
    if stopped:
        out["stopped"] = f"中途停下：{stopped}。别再接着查这个平台，告诉用户"
    if not items and not listed:
        out["hint"] = "平台上没搜到。可以换个更短的词；确实没有就如实告诉用户"
    return out


def _search_xhs(context: Any, query: str, limit: int) -> tuple[list[RawItemIn], int, str | None, list[dict[str, Any]]]:
    from polaris.collectors.xhs import XhsCollector, note_url, worth_opening
    from polaris.collectors.xhs_browser import BrowserCaller

    caller = BrowserCaller(context)
    opened = min(2, limit)
    col = XhsCollector(max_calls=1 + opened, pause=PAUSE["xhs"], caller=caller, open_profiles=False, known=set())
    try:
        result = col._call("search_feeds", {"keyword": query, "filters": {"sort_by": "最新", "publish_time": "一周内"}})
        feeds = [f for f in (result or {}).get("feeds") or [] if f.get("id") and f.get("xsecToken")]
        items: list[RawItemIn] = []
        for feed in sorted(feeds, key=lambda f: not worth_opening(f))[:opened]:  # 先开标题像求职内容的
            item = col._open(feed["id"], feed["xsecToken"], query)
            if item:
                items.append(item)
        done = {i.external_id for i in items}
        listed = [{"title": (f.get("noteCard") or {}).get("displayTitle"), "url": note_url(f["id"], f["xsecToken"]),
                   "likes": ((f.get("noteCard") or {}).get("interactInfo") or {}).get("likedCount")}
                  for f in feeds if f["id"] not in done][: max(0, limit - len(items))]
        return items, col.stats.calls, col.stats.stopped, listed
    finally:
        caller.close()


def _search_maimai(context: Any, query: str, limit: int) -> tuple[list[RawItemIn], int, str | None, list[dict[str, Any]]]:
    from polaris.collectors.base import Query
    from polaris.collectors.maimai import MaimaiCollector

    col = MaimaiCollector(context, max_calls=1, pause=PAUSE["maimai"], max_pages=1, known=set())
    try:
        items = list(islice(col.collect(Query(keywords=[query], max_pages=1)), limit))
        return items, col.stats.calls, col.stats.stopped, []
    finally:
        col.close()


def _search_boss(query: str, limit: int, city: str | None) -> tuple[list[RawItemIn], int, str | None, list[dict[str, Any]]]:
    from polaris.collectors.base import Query
    from polaris.collectors.boss import BossCollector

    col = BossCollector(cities=(city or "北京",), scales=("10000人以上",), max_calls=1 + min(2, limit), pause=PAUSE["boss"],
                        known=set())
    items = list(islice(col.collect(Query(keywords=[query], max_pages=1)), limit))
    return items, col.stats.calls, col.stats.stopped, []


def _search_tencent(query: str, limit: int) -> tuple[list[RawItemIn], int, str | None, list[dict[str, Any]]]:
    from polaris.collectors.base import Query
    from polaris.collectors.tencent import TencentCollector

    col = TencentCollector(page_size=min(10, limit), with_detail=True)
    items = list(islice(col.collect(Query(keywords=[query], max_pages=1)), limit))
    return items, 1 + len(items), None, []


# ---------- 纵向：打开原链接 ----------

def _target(ref: str) -> RawItem:
    """J- 取这个岗位最可信的一条挂牌（官网优先、最新），P- 取帖子的原文，R- 就是原始条目本身。"""
    kind, _, num = ref.partition("-")
    if not num.isdigit() or kind not in ("J", "P", "R"):
        raise LiveUnavailable("编号不对，只能打开 J-（岗位）、P-（帖子）、R-（刚查到的）")
    with session_scope() as s:
        if kind == "R":
            raw = s.get(RawItem, int(num))
        elif kind == "P":
            post = s.get(Post, int(num))
            raw = s.get(RawItem, post.raw_item_id) if post else None
        else:
            if not s.get(Job, int(num)):
                raw = None
            else:
                rows = s.scalars(select(RawItem).join(Posting, Posting.raw_item_id == RawItem.id)
                                 .where(Posting.job_id == int(num)).order_by(RawItem.fetched_at.desc())).all()
                rows = sorted(rows, key=lambda r: r.source in LOGIN_SOURCES or r.source.startswith("synthetic"))
                raw = rows[0] if rows else None
        if raw is None:
            raise LiveUnavailable(f"{ref} 不存在或没有原始来源")
        s.expunge(raw)
        return raw


def _fetch_public_page(url: str, source: str) -> str:
    from polaris.collectors.sites import browser

    with browser() as b:
        page = b.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:  # 正文晚于标题渲染
            page.wait_for_function("() => /职位描述|岗位职责|工作职责|职位要求|任职|下线|不存在/.test(document.body.innerText)",
                                   timeout=20000)
        except Exception:  # noqa: BLE001
            pass
        text = page.inner_text("body")
    for head in ("职位描述", "岗位职责", "工作职责"):
        if head in text:
            text = text[max(0, text.find(head) - 300):]
            break
    for tail in ("\n投递\n", "相关职位", "社区精选", "联系我们"):  # 正文之后是相关职位、页脚和客服弹窗
        if tail in text:
            text = text[:text.find(tail)]
    return text.strip()[:6000]


def _refetch(raw: RawItem) -> tuple[str | None, int]:
    """重新取一次。返回（新正文，用了几次调用）；正文 None 表示页面已经没了。"""
    src = raw.source
    if src == "tencent:careers":
        from polaris.collectors.tencent import TencentCollector, to_text

        try:
            post = TencentCollector(with_detail=False).detail(raw.external_id or "")
        except RuntimeError:
            return None, 1
        return (to_text({**(raw.raw_json or {}), **post}) if post else None), 1
    if src in ("bytedance:campus", "xhs:careers"):
        text = _fetch_public_page(raw.url or "", src)
        gone = any(w in text for w in GONE_WORDS) or len(text) < 80
        return (None if gone else text), 1
    if src == "xhs":
        from polaris.collectors.xhs import slim_comments, to_text
        from polaris.collectors.xhs_browser import BrowserCaller

        token = (parse_qs(urlparse(raw.url or "").query).get("xsec_token") or [None])[0]
        if not token:
            raise LiveUnavailable("这条小红书笔记没存访问令牌，打不开原链接；可以用 platform_search 按标题再搜一次")
        with _context("xhs") as context:
            _check_login(context, "xhs")
            caller = BrowserCaller(context)
            try:
                detail = caller.detail({"feed_id": raw.external_id, "xsec_token": token})
            finally:
                caller.close()
        note = ((detail or {}).get("data") or {}).get("note") or {}
        if not note:
            return None, 1
        comments = slim_comments((((detail.get("data") or {}).get("comments")) or {}).get("list") or [], limit=20)
        return to_text(note, comments), 1
    if src == "maimai":
        with _context("maimai") as context:
            _check_login(context, "maimai")
            page = context.new_page()
            try:
                page.goto(raw.url or "", wait_until="domcontentloaded", timeout=45000)
                time.sleep(random.uniform(*PAUSE["maimai"]))
                text = page.inner_text("body")[:6000]
            finally:
                page.close()
        if any(w in text[:2000] for w in BLOCK_WORDS):
            raise LiveUnavailable("脉脉出现了验证或限流，先停下；常驻采集也会按规矩冷却")
        return (None if any(w in text for w in GONE_WORDS) else text), 1
    if src == "boss":
        raise LiveUnavailable("Boss 的岗位暂时不能单独打开原链接（需要当时的安全令牌）；可以用 platform_search 在 Boss 上现搜")
    raise LiveUnavailable(f"{label(src)}的来源不支持重新打开")


def _substance(text: str) -> dict[str, str]:
    """有内容的句子（职责、要求、正文、评论）：去掉空白和标号后至少 12 个字的行，按出现顺序去重。"""
    import re

    out: dict[str, str] = {}
    for line in text.splitlines():
        if re.match(r"\s*(职位\s*ID|地点|类型|类别|公司|发布|更新时间|经验要求)\s*[：:]", line):
            continue  # 元信息：页面和接口存下来的写法不同，比较没意义
        key = re.sub(r"^[\d一二三四五六七八九十]+[、.．)）]\s*|[\s，,。；;：:]", "", line.strip())
        if len(key) >= 12 and key not in out:
            out[key] = line.strip()
    return out


def open_source(ref: str) -> dict[str, Any]:
    from polaris.tools.base import untrusted

    raw = _target(ref)
    src = raw.source
    if src.startswith("synthetic"):
        raise LiveUnavailable("这是合成的演示数据，没有真实链接")
    _budget_ok(src)
    try:
        with platform_lock(src, timeout=40):
            text, calls = _refetch(raw)
    except PlatformBusy as e:
        raise LiveUnavailable(f"{e}，等它这一轮结束再查") from e
    if src in LOGIN_SOURCES:
        _record(src, None, calls, None, int(text is not None))
    now = datetime.now(UTC)
    out: dict[str, Any] = {"ref": ref, "source": label(src), "url": raw.url, "checked_at": now.isoformat(timespec="seconds"),
                           "stored_at": raw.fetched_at.isoformat(timespec="seconds") if raw.fetched_at else None}
    if text is None:
        out.update(online=False, note="原链接打开了，但页面显示已下线、被删或不存在")
        return out
    old_lines, new_lines = _substance(raw.raw_text or ""), _substance(text)
    diff = [d for d in difflib.unified_diff(list(old_lines), list(new_lines), lineterm="", n=0)
            if d[:1] in "+-" and not d.startswith(("+++", "---"))]
    # 只比有内容的句子：网页上的导航、按钮、页脚和接口存下来的格式不一样，不算改动（10-12 实测字节官网误报）
    changed = set(old_lines) != set(new_lines) and bool(diff)
    shown = {**old_lines, **new_lines}
    out.update(online=True, changed=changed, changes=[(d[0] + shown.get(d[1:], d[1:]))[:150] for d in diff[:24]],
               text=untrusted(text[:3000]))
    if changed:  # 新版本写回原始层，下一轮对齐会更新岗位或帖子
        item = RawItemIn(source=src, tier=raw.tier, kind=raw.kind, url=raw.url, external_id=raw.external_id,
                         query=raw.query, raw_text=text, raw_json=raw.raw_json or {}, fetched_at=now,
                         fetch_method="live", collector_version=f"{raw.collector_version or src}+live"[:32],
                         published_at=raw.published_at)
        rid = _store([item]).get((src, content_hash(text)))
        out["new_version"] = f"R-{rid}" if rid else None
    return out


def raw_card(raw: RawItem) -> dict[str, Any]:
    """R- 编号在聊天里的卡片：刚现查到或现打开的原文。"""
    lines = [line.strip() for line in (raw.raw_text or "").splitlines() if line.strip()]
    return {"id": f"R-{raw.id}", "kind": "raw", "display_title": (lines[0] if lines else label(raw.source))[:80],
            "summary": " ".join(lines[1:4])[:160], "company": None, "published": raw.published_at.date().isoformat()
            if raw.published_at else None, "sources": [source_dict(raw.source, raw.url, raw_id=raw.id, fetched_at=raw.fetched_at,
                                                                  published_at=raw.published_at)],
            "text": raw.raw_text, "material_label": "刚现查到的" if raw.fetch_method == "live" or raw.run_id is None else None}
