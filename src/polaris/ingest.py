"""原始条目入库。采集器、手动粘贴、本机 B 档采集器推上来的，都走这里。同一来源、同一内容只存一次。"""
import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert

from polaris.db.models import RawItem
from polaris.db.session import session_scope


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
    is_synthetic: bool = False


class IngestResult(BaseModel):
    received: int
    inserted: int
    duplicates: int
    ids: list[int]


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
        "content_hash": content_hash(item.raw_text),
        "run_id": run_id,
    } for item in items]
    ids: list[int] = []
    if rows:
        stmt = insert(RawItem).values(rows).on_conflict_do_nothing(
            constraint="uq_raw_items_source_hash").returning(RawItem.id)
        with session_scope(engine) as session:
            ids = list(session.execute(stmt).scalars())
    return IngestResult(received=len(items), inserted=len(ids), duplicates=len(items) - len(ids), ids=ids)
