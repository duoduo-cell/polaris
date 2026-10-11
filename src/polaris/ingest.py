"""原始条目入库。采集器、手动粘贴、本机 B 档采集器推上来的，都走这里。

同一来源、同一内容只存一次；再次采到时只更新"最后一次采到"的时间，官网岗位据此推出什么时候下架。
"""
import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import Engine, literal_column
from sqlalchemy.dialects.postgresql import insert

from polaris.db.models import RawItem
from polaris.db.session import session_scope
from polaris.dedup import simhash64


class RawItemIn(BaseModel):
    source: str = Field(max_length=64, description="来源，如 greenhouse、tavily、manual、xhs")
    tier: Literal["A", "B"] = "A"
    kind: Literal["job", "post"] = "job"
    url: str | None = None
    external_id: str | None = Field(None, max_length=200)
    query: str | None = Field(None, description="采到它的查询词")
    raw_text: str = Field(min_length=1)
    raw_json: dict[str, Any] | None = None
    fetched_at: datetime | None = None
    published_at: datetime | None = Field(None, description="来源上写的发布或更新时间")
    fetch_method: str | None = Field(None, max_length=16, description="api / browser / mcp / cli / sitemap / archive / manual")
    collector_version: str | None = Field(None, max_length=32)
    is_synthetic: bool = False


class IngestResult(BaseModel):
    received: int
    inserted: int
    duplicates: int
    ids: list[int]  # 只含新写入的
    seen_again: int = 0  # 已经有的、这次又采到的（更新了最后一次采到的时间）


def content_hash(text: str) -> str:
    """空白不同算同一份内容。"""
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def ingest_raw_items(items: list[RawItemIn], run_id: int | None = None,
                     engine: Engine | None = None) -> IngestResult:
    now = datetime.now(UTC)
    rows = [{
        **item.model_dump(exclude={"fetched_at"}),
        "fetched_at": item.fetched_at or now,
        "first_seen_at": now,
        "last_seen_at": now,
        "content_hash": content_hash(item.raw_text),
        "simhash": simhash64(item.raw_text),
        "run_id": run_id,
    } for item in items]
    rows = list({(r["source"], r["content_hash"]): r for r in rows}.values())  # 同一批里重复的只留一条
    ids: list[int] = []
    seen_again = 0
    if rows:
        stmt = insert(RawItem).values(rows)
        stmt = stmt.on_conflict_do_update(constraint="uq_raw_items_source_hash",
                                          set_={"last_seen_at": stmt.excluded.last_seen_at}
                                          ).returning(RawItem.id, literal_column("(xmax = 0)"))  # xmax = 0 表示新插入
        with session_scope(engine) as session:
            for rid, inserted in session.execute(stmt).all():
                if inserted:
                    ids.append(rid)
                else:
                    seen_again += 1
    return IngestResult(received=len(items), inserted=len(ids), duplicates=len(items) - len(ids), ids=ids,
                        seen_again=seen_again)
