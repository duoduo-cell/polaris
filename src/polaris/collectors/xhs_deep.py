"""小红书深挖：重新打开已经采到的笔记，把图片里的字读出来、评论往下翻完，补进原始数据（10-12）。

为什么：10-12 查库，416 条笔记里 408 条是图文笔记，采集时只留了标题和正文，图片一张没存；评论每条最多 10 条。
可是求职者最想看的"拿到 offer 的人什么背景、几篇论文、几段实习、挂在几面"，常常在简历截图、长图和评论区里。

规矩（`DATASET.md` 第 4 节）：
- 只重新打开已经采到的笔记，不额外搜索；和常驻采集共用一把锁、每天的额度和每小时的上限，现查的记账方式一样；
- 用你本人登录的采集浏览器，像人一样停顿；页面出现限流或验证就立刻停下；
- 图片只在本机读成文字后存文字，图片本身不入库；评论只留内容、点赞、时间、属地，不留评论者。
"""
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from polaris.collectors.xhs import slim_comments, to_text
from polaris.collectors.xhs_browser import STATE_NOTE, BrowserCaller, BrowserCallFailed
from polaris.db.models import PeerProfile, Post, RawItem
from polaris.db.session import session_scope
from polaris.ingest import content_hash

VERSION = "xhs-deep-1"
MAX_IMAGES = 9
MAX_COMMENTS = 80
COMMENT_SCROLLS = 24  # 每次加载 10 条，要滚两三次；80 条大约 20 次
OCR_PROMPT = "把图片里的文字原样转写出来，保持原来的分行和顺序；看不清的字用□代替；没有文字就只回答“无文字”。不要解释。"
# 先挑最可能有"人和结果"的笔记：提到 offer、背景、简历、论文、实习经历、几面的
SIGNALS = r"offer|上岸|oc|意向|bg|背景|简历|履历|论文|ccf|顶会|一作|实习经历|段实习|一面|二面|三面|终面|hr面|转正|挂了|凉了"


def clean_ocr(text: str) -> str:
    """识别模型有时把表格、排版图转成 HTML 或包一层代码块（10-12 首批 34 张图里标签占 7%），去掉标记只留字。"""
    import re

    text = re.sub(r"^```\w*\s*$", "", text, flags=re.M)
    text = re.sub(r"<br\s*/?>|</(?:p|h\d|li|tr|div)>", "\n", text, flags=re.I)
    text = re.sub(r"</t[dh]>", " ", text, flags=re.I)
    text = re.sub(r"<[^>\n]{1,200}>", "", text)
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "\n".join(line.strip() for line in text.splitlines())).strip()


def brief(text: str, limit: int = 2400) -> str:
    """给模型看的精简版：正文、图片里的字、评论各占一段，免得截断时把图片和评论整段丢掉（它们排在最后）。"""
    body, _, image = text.partition("\n\n图片里的文字：\n")
    main, _, comments = body.partition("\n\n评论：\n")
    parts = [main[: limit * 2 // 5]]
    if image:
        parts.append("图片里的文字：\n" + image[: limit * 2 // 5])
    if comments:
        parts.append("评论：\n" + comments[: limit // 5])
    return "\n\n".join(parts)


def candidates(limit: int) -> list[int]:
    """还没深挖过、带访问令牌的小红书笔记，按"像不像有背景和结果"、再按新旧排。"""
    import re

    with session_scope() as s:
        rows = s.execute(select(RawItem.id, RawItem.url, RawItem.raw_text, RawItem.raw_json, RawItem.published_at)
                         .where(RawItem.source == "xhs", RawItem.kind == "post", RawItem.is_synthetic.is_(False))).all()
    now = datetime.now(UTC)
    scored = []
    for rid, url, text, rj, published in rows:
        if (rj or {}).get("deep") or "xsec_token=" not in (url or ""):
            continue
        hits = len(set(m.lower() for m in re.findall(SIGNALS, text or "", re.I)))
        age = (now - published).days if published else 999
        scored.append((hits * 3 + (2 if age <= 30 else 1 if age <= 90 else 0), -age, rid))
    return [rid for *_, rid in sorted(scored, reverse=True)[:limit]]


def _image_urls(note: dict[str, Any]) -> list[str]:
    urls = []
    for img in (note.get("imageList") or [])[:MAX_IMAGES]:
        url = img.get("urlDefault") or img.get("url") or next(
            (i.get("url") for i in img.get("infoList") or [] if i.get("imageScene") in ("WB_DFT", "CRD_WM_WEBP")), None)
        if url:
            urls.append(url)
    return urls


def _more_comments(caller: BrowserCaller, note_id: str, first: dict[str, Any]) -> list[dict[str, Any]]:
    """评论区往下翻几次，直到没有新的或到上限；读页面自己的状态数据，不另外调接口。"""
    best = (first or {}).get("list") or []
    # 10-12 实测：鼠标不在评论区上时滚轮不起作用（库里每条都停在第一页 10 条，帖子显示 60–78 条）；
    # 放上去以后要滚两三次到底才加载下一页 10 条，所以连续几次没变多才停
    try:
        box = caller.page.locator(".note-scroller").bounding_box(timeout=3000)
        if box:
            caller.page.mouse.move(box["x"] + box["width"] * random.uniform(0.3, 0.7),
                                   box["y"] + box["height"] * random.uniform(0.5, 0.8))
    except Exception:  # noqa: BLE001  没有评论区就用第一页
        return best[:MAX_COMMENTS]
    still = 0
    for _ in range(COMMENT_SCROLLS):
        if len(best) >= MAX_COMMENTS or not (first or {}).get("hasMore", True) or still >= 3:
            break
        try:
            caller.page.mouse.wheel(0, random.randint(1200, 2000))
            caller.sleep(random.uniform(1.2, 2.4))
            entry = caller._eval_json(STATE_NOTE, note_id) or {}
        except Exception:  # noqa: BLE001  翻不动就用已有的
            break
        now = (entry.get("comments") or {}).get("list") or []
        if len(now) <= len(best):
            still += 1
            continue
        best, first, still = now, entry.get("comments") or {}, 0
    return best[:MAX_COMMENTS]


def _download(caller: BrowserCaller, url: str) -> tuple[bytes, str] | None:
    """用采集浏览器自己的请求去取图（和页面上显示这张图是同一个请求），失败就跳过这张。"""
    try:
        r = caller.page.request.get(url, headers={"Referer": "https://www.xiaohongshu.com/"}, timeout=30000)
        if not r.ok:
            return None
        return r.body(), (r.headers.get("content-type") or "image/jpeg").split(";")[0]
    except Exception:  # noqa: BLE001
        return None


def _ocr(images: list[tuple[bytes, str]], run_id: int | None) -> list[str]:
    from polaris.llm.client import LLM

    llm = LLM()

    def one(img: tuple[bytes, str]) -> str:
        try:
            text = (llm.vision(OCR_PROMPT, img[0], mime=img[1], purpose="xhs_ocr", run_id=run_id,
                               prompt_version="xhs-ocr-v1").content or "").strip()
        except Exception as e:  # noqa: BLE001  一张读不出来不影响别的
            return f"（这张没读出来：{type(e).__name__}）"
        text = clean_ocr(text)
        return "" if text in ("无文字", "无文字。") else text

    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(one, images))


def enrich(caller: Any, note_id: str, note: dict[str, Any], comments_state: dict[str, Any],
           run_id: int | None = None) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """同一次浏览里把评论翻完、图片读成字，拼成原始正文和 raw_json。常驻采集打开新笔记、补老笔记都用它。
    返回（正文，raw_json，统计）。"""
    comments = slim_comments(_more_comments(caller, note_id, comments_state), limit=MAX_COMMENTS)
    urls = _image_urls(note)
    images = [img for img in (_download(caller, u) for u in urls) if img]
    ocr = _ocr(images, run_id)
    text = to_text(note, comments)
    readable = [(i + 1, t) for i, t in enumerate(ocr) if t]
    if readable:
        text += "\n\n图片里的文字：\n" + "\n\n".join(f"[图{i}]\n{t}" for i, t in readable)
    kept = {k: note.get(k) for k in ("noteId", "title", "desc", "type", "time", "ipLocation", "interactInfo")}
    kept["user"] = {k: (note.get("user") or {}).get(k) for k in ("userId", "nickname")}
    stats = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "version": VERSION, "images": len(urls),
             "images_read": len(images), "images_with_text": len(readable),
             "ocr_chars": sum(len(t) for _, t in readable), "comments": len(comments)}
    raw_json = {"note": kept, "comments": comments, "images": [{"i": i, "ocr": t} for i, t in readable], "deep": stats}
    return text, raw_json, stats


def deepen_one(caller: BrowserCaller, raw_id: int, run_id: int | None = None) -> dict[str, Any]:
    """补一条老笔记：重新打开、读图、翻评论，写回原始数据。返回这条的统计。"""
    with session_scope() as s:
        raw = s.get(RawItem, raw_id)
        url, note_id, old_text, old_json = raw.url, raw.external_id, raw.raw_text or "", dict(raw.raw_json or {})
    token = (parse_qs(urlparse(url or "").query).get("xsec_token") or [None])[0]
    detail = caller.detail({"feed_id": note_id, "xsec_token": token})
    data = (detail or {}).get("data") or {}
    note = data.get("note") or {}
    if not note:
        with session_scope() as s:
            s.get(RawItem, raw_id).raw_json = {**old_json, "deep": {"at": datetime.now(UTC).isoformat(timespec="seconds"),
                                                                    "version": VERSION, "gone": True}}
        return {"id": raw_id, "gone": True}
    text, raw_json, stats = enrich(caller, note_id, note, data.get("comments") or {}, run_id)
    stats["comments_before"] = len(old_json.get("comments") or [])
    raw_json["before"] = {"chars": len(old_text), "comments": stats["comments_before"]}
    with session_scope() as s:
        raw = s.get(RawItem, raw_id)
        raw.raw_text, raw.content_hash, raw.raw_json = text, content_hash(text), raw_json
        raw.fetched_at, raw.collector_version = datetime.now(UTC), VERSION
        # 正文变了（多了图片里的字和评论）：旧的抽取结果作废，下一轮带着新内容重抽
        s.query(Post).filter(Post.raw_item_id == raw_id).delete()
        s.query(PeerProfile).filter(PeerProfile.raw_item_id == raw_id).delete()
    return {"id": raw_id, **stats, "chars_before": len(old_text), "chars_after": len(text)}


def backfill(caller: BrowserCaller, limit: int, pause: tuple[float, float] = (5.0, 10.0)) -> dict[str, Any]:
    """在已经拿到锁和浏览器的前提下补一批老笔记（常驻采集每轮留出的那部分额度用这个）。被拦就停；调用记进额度。"""
    from polaris.live import LiveUnavailable, _budget_ok, _record

    ids = candidates(limit)
    done: list[dict[str, Any]] = []
    stopped = None
    calls = 0
    try:
        for rid in ids:
            try:
                _budget_ok("xhs")
            except LiveUnavailable as e:
                stopped = str(e)
                break
            try:
                calls += 1
                done.append(deepen_one(caller, rid))
            except BrowserCallFailed as e:  # 限流、验证：立刻停，常驻采集会按规矩冷却
                stopped = str(e)
                break
            time.sleep(random.uniform(*pause))
    finally:
        if calls:
            _record("xhs", "deepen", calls, stopped, len(done))
    return {"candidates": len(ids), "deepened": len(done), "stopped": stopped, "items": done}


def deepen(limit: int) -> dict[str, Any]:
    """手动补一批（polaris deepen）：自己拿锁、接上采集浏览器。"""
    from polaris.collectors.locks import platform_lock
    from polaris.live import _check_login, _context

    with platform_lock("xhs", timeout=900), _context("xhs") as context:
        _check_login(context, "xhs")
        caller = BrowserCaller(context)
        try:
            return backfill(caller, limit)
        finally:
            caller.close()
