"""raw_items_*：查原始条目（采集到的原文）。M2 用它们验证工具层和 agent 底座；正式检索在 M4 做。"""
import re
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import and_, select

from polaris.db.models import RawItem
from polaris.db.session import session_scope
from polaris.tools.base import Page, ToolError, ToolSpec, untrusted


def short_id(item_id: int) -> str:
    return f"R-{item_id}"


def parse_id(value: str) -> int:
    m = re.fullmatch(r"\s*(?:R-)?(\d+)\s*", value, re.I)
    if not m:
        raise ToolError(f"编号「{value}」格式不对", "用 raw_items_search 结果里的编号，形如 R-12", '{"item_id": "R-12"}')
    return int(m.group(1))


def first_line(text: str, limit: int = 60) -> str:
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line if len(line) <= limit else line[:limit] + "…"


def snippet(text: str, word: str, width: int = 60) -> str:
    flat = re.sub(r"\s+", " ", text)
    i = flat.lower().find(word.lower())
    if i < 0:
        return flat[: width * 2]
    start = max(0, i - width)
    return ("…" if start else "") + flat[start: i + len(word) + width] + "…"


class RawItemsSearchArgs(Page):
    query: str = Field(..., min_length=1, max_length=100,
                       description="要在原文里找的词，多个词用空格分开，全部包含才算命中。如：穹宇集团 Agent")
    source: str | None = Field(None, description="只看某个来源，如 synthetic:official；不填看全部")


def raw_items_search(args: RawItemsSearchArgs) -> dict:
    words = args.query.split()
    with session_scope() as s:
        stmt = select(RawItem).where(and_(*[RawItem.raw_text.ilike(f"%{w}%") for w in words]))
        if args.source:
            stmt = stmt.where(RawItem.source == args.source)
        rows = s.execute(stmt.order_by(RawItem.id).limit(args.limit + 1).offset(args.offset)).scalars().all()
    has_more = len(rows) > args.limit
    items = []
    for r in rows[: args.limit]:
        item = {"id": short_id(r.id), "source": r.source, "title": first_line(r.raw_text),
                "snippet": snippet(r.raw_text, words[0])}
        if args.response_format == "detailed":
            item.update({"url": r.url, "fetched_at": r.fetched_at.isoformat(), "external_id": r.external_id,
                         "length": len(r.raw_text)})
        items.append(item)
    result: dict = {"returned": len(items), "items": items,
                    "next_offset": args.offset + args.limit if has_more else None}
    if not items:
        result["hint"] = "没有同时包含这些词的原文。可以少用几个词、换更通用的说法，或去掉 source 限制。"
    return result


class RawItemsGetArgs(BaseModel):
    item_id: str = Field(..., description="原始条目编号，如 R-12，来自 raw_items_search 的结果")
    response_format: Literal["concise", "detailed"] = Field(
        "concise", description="concise 给原文；detailed 再给链接、抓取时间等")


def raw_items_get(args: RawItemsGetArgs) -> dict:
    item_id = parse_id(args.item_id)
    with session_scope() as s:
        r = s.get(RawItem, item_id)
        if r is None:
            raise ToolError(f"没有编号为 {short_id(item_id)} 的原始条目",
                            "先用 raw_items_search 找到编号，再用这个工具取全文", '{"item_id": "R-12"}')
        out = {"id": short_id(r.id), "source": r.source, "url": r.url, "text": untrusted(r.raw_text)}
        if args.response_format == "detailed":
            out.update({"fetched_at": r.fetched_at.isoformat(), "external_id": r.external_id,
                        "content_hash": r.content_hash[:12], "tier": r.tier})
    return out


TOOLS = [
    ToolSpec(
        name="raw_items_search",
        description="在采集到的原文里按关键词查找，返回编号、标题和命中片段。要看全文再用 raw_items_get。",
        args_schema=RawItemsSearchArgs, func=raw_items_search, example='{"query": "穹宇集团 Agent", "limit": 5}'),
    ToolSpec(
        name="raw_items_get",
        description="按编号取一条原始条目的全文。编号来自 raw_items_search。",
        args_schema=RawItemsGetArgs, func=raw_items_get, example='{"item_id": "R-12"}'),
]
