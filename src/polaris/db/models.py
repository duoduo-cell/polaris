"""数据表。M1 只建 M1 到 M3 用得到的六张表，后面的表到对应模块用迁移加。"""
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from polaris.config import EMBED_DIM


class Base(DeclarativeBase):
    pass


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Run(Base):
    """一次采集、抽取、评测、入库或生成。stats 里放这次的指标，git_commit 记当时的代码版本。"""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running / ok / failed
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    git_commit: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime] = created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LLMCall(Base):
    """每一次模型调用，包括失败的重试和读缓存的。评测页和回放都从这里取数。"""

    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("ix_llm_calls_purpose_created", "purpose", "created_at"),
        Index("ix_llm_calls_cache_key", "cache_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    purpose: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(128))
    model: Mapped[str] = mapped_column(String(128))
    endpoint: Mapped[str] = mapped_column(String(16))  # chat / embeddings / rerank
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    parsed_ok: Mapped[bool | None] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cache_key: Mapped[str | None] = mapped_column(String(64))
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at()


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    is_big: Mapped[bool] = mapped_column(Boolean, default=False)  # 大厂，汇总时单列
    ats: Mapped[str | None] = mapped_column(String(32))  # greenhouse / lever / ashby / official
    ats_board: Mapped[str | None] = mapped_column(String(200))
    careers_url: Mapped[str | None] = mapped_column(Text)
    discovered_from: Mapped[str | None] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at()


class RawItem(Base):
    """采集器的统一输出：来源、链接、抓取时间、原文、内容哈希（PLAN 第 4 节）。"""

    __tablename__ = "raw_items"
    __table_args__ = (UniqueConstraint("source", "content_hash", name="uq_raw_items_source_hash"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    tier: Mapped[str] = mapped_column(String(1), default="A")  # A 进公开演示，B 只在本机
    kind: Mapped[str] = mapped_column(String(16), default="job")  # job / post
    url: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(200))
    query: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at()


class JobFields:
    """岗位的业务字段，postings 和 jobs 共用。字段来自 PRODUCT 第 5 节和 PLAN 第 4.2 节，抽不到的留空。"""

    company_name: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(Text)
    directions: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    jd_text: Mapped[str | None] = mapped_column(Text)
    requirements: Mapped[list[str]] = mapped_column(JSONB, default=list)
    city: Mapped[str | None] = mapped_column(String(100))
    salary_text: Mapped[str | None] = mapped_column(String(200))
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_unit: Mapped[str | None] = mapped_column(String(8))  # day / month / year
    job_type: Mapped[str | None] = mapped_column(String(16))  # intern / campus / social
    can_convert: Mapped[bool | None] = mapped_column(Boolean)
    posted_at: Mapped[date | None] = mapped_column(Date)
    deadline: Mapped[date | None] = mapped_column(Date)
    start_text: Mapped[str | None] = mapped_column(String(200))
    start_date: Mapped[date | None] = mapped_column(Date)
    duration_months: Mapped[int | None] = mapped_column(Integer)
    days_per_week: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    embed_model: Mapped[str | None] = mapped_column(String(128))


class Job(JobFields, Base):
    """多来源合并后的一条岗位（M3 起写入）。field_sources 记每个字段的值取自哪条 posting。"""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_embedding", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))
    field_sources: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / gone
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)  # 第一次采集的存量，不算新增
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class Posting(JobFields, Base):
    """一条原始条目抽出的岗位（M2 起写入）。一条原文可能抽出多条，比如汇总帖。"""

    __tablename__ = "postings"
    __table_args__ = (
        Index("ix_postings_embedding", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(ForeignKey("raw_items.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    llm_call_id: Mapped[int | None] = mapped_column(ForeignKey("llm_calls.id", ondelete="SET NULL"))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at()
