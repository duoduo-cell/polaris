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


class ChatThread(Base):
    """展示记录的入口；模型上下文仍由 LangGraph 独立管理。"""

    __tablename__ = "chat_threads"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    history_imported: Mapped[bool] = mapped_column(Boolean, default=False)
    approvals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = created_at()


class ChatMessage(Base):
    """用户原话和正式回复。摘要、工具调用及系统提示永远不写入这里。"""

    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("thread_id", "source_key"), Index("ix_chat_messages_thread", "thread_id", "id"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"))
    source_key: Mapped[str] = mapped_column(String(192))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    turn: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()


class ChatTask(Base):
    """持久化任务；浏览器只提交和观察，不拥有执行线程。"""

    __tablename__ = "chat_tasks"
    __table_args__ = (Index("ix_chat_tasks_thread", "thread_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(24), default="queued")
    activities: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class ToolCall(Base):
    """每一次工具调用，包括参数校验失败的和回放的。评测工具选择、参数正确率、出错恢复都从这里取数。"""

    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_run_tool", "run_id", "tool"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    thread_id: Mapped[str | None] = mapped_column(String(64))  # 同一次对话或任务
    agent: Mapped[str | None] = mapped_column(String(64))  # 哪个角色调的
    tool: Mapped[str] = mapped_column(String(64))
    args: Mapped[dict[str, Any]] = mapped_column(JSONB)
    output: Mapped[str | None] = mapped_column(Text)
    ok: Mapped[bool] = mapped_column(Boolean)
    error_kind: Mapped[str | None] = mapped_column(String(32))  # invalid_args / tool_error / internal / injected
    latency_ms: Mapped[int] = mapped_column(Integer)
    replayed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at()


class Post(Base):
    """小红书笔记（面经、内推、招聘、经验帖）抽出的信息，一条原文一行，可追溯到原文。"""

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(ForeignKey("raw_items.id", ondelete="CASCADE"), unique=True)
    post_type: Mapped[str] = mapped_column(String(16))
    companies: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    big_companies: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)  # 按大厂名单归一后的
    team: Mapped[str | None] = mapped_column(String(200))
    roles: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    directions: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    city: Mapped[str | None] = mapped_column(String(100))
    job_type: Mapped[str | None] = mapped_column(String(16))
    grad_year: Mapped[str | None] = mapped_column(String(50))
    days_per_week: Mapped[int | None] = mapped_column(Integer)
    duration_months: Mapped[int | None] = mapped_column(Integer)
    can_convert: Mapped[bool | None] = mapped_column(Boolean)
    has_referral: Mapped[bool] = mapped_column(Boolean, default=False)
    interview_rounds: Mapped[int | None] = mapped_column(Integer)
    interview_questions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    advice: Mapped[list[str]] = mapped_column(JSONB, default=list)
    summary: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    likes: Mapped[int | None] = mapped_column(Integer)
    llm_call_id: Mapped[int | None] = mapped_column(ForeignKey("llm_calls.id", ondelete="SET NULL"))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    evidence_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = created_at()


class InterviewQuestion(Base):
    """题目与原帖证据；公司归属不再继承全文提及公司的集合。"""

    __tablename__ = "interview_questions"
    key: Mapped[str] = mapped_column(String(80), unique=True)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(String(200), index=True)
    role: Mapped[str | None] = mapped_column(String(200))
    team: Mapped[str | None] = mapped_column(String(200))
    job_type: Mapped[str | None] = mapped_column(String(16))
    round: Mapped[str | None] = mapped_column(String(50))
    category: Mapped[str] = mapped_column(String(32))
    material: Mapped[str] = mapped_column(String(16))  # firsthand / study / uncertain
    evidence: Mapped[str | None] = mapped_column(Text)
    attribution: Mapped[str] = mapped_column(String(16))  # explicit / unknown
    interview_date: Mapped[date | None] = mapped_column(Date)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobFollowup(Base):
    """用户明确选择的岗位状态；收藏不等同投递。"""

    __tablename__ = "job_followups"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True)
    saved: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="untracked")
    stage: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    next_followup: Mapped[date | None] = mapped_column(Date)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobFollowupEvent(Base):
    __tablename__ = "job_followup_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    before: Mapped[dict[str, Any]] = mapped_column(JSONB)
    after: Mapped[dict[str, Any]] = mapped_column(JSONB)
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class SearchQuery(Base):
    """各来源的查询词。每轮挑最久没搜过的那批轮换着搜；从结果里发现的新说法也加进来（origin=discovered）。"""

    __tablename__ = "search_queries"
    __table_args__ = (UniqueConstraint("source", "query", name="uq_search_queries_source_query"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    query: Mapped[str] = mapped_column(String(200))
    origin: Mapped[str] = mapped_column(String(16), default="seed")  # seed / expanded / discovered
    priority: Mapped[int] = mapped_column(Integer, default=0)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    new_items: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    __table_args__ = (UniqueConstraint("source", "content_hash", name="uq_raw_items_source_hash"),
                      Index("ix_raw_items_source_external", "source", "external_id"))

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
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 来源上写的发布或更新时间
    # 第一次、最后一次采到同一份内容；官网岗位据此推出挂出和下架时间（DATASET 2.1）
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    fetch_method: Mapped[str | None] = mapped_column(String(16))  # api / browser / mcp / cli / sitemap / archive / manual
    collector_version: Mapped[str | None] = mapped_column(String(32))
    simhash: Mapped[int | None] = mapped_column(BigInteger)  # 近似重复指纹（64 位，存成有符号整数）
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
    # 稳定编号：公司 | 最具体的部门 | 岗位名（align/match.py 的 job_key）。重建时编号不变，引用不失效
    key: Mapped[str | None] = mapped_column(String(300), unique=True)
    company: Mapped[str | None] = mapped_column(String(200))  # 归一后的公司（大厂名单里的规范名）
    unit: Mapped[str | None] = mapped_column(String(200))  # 部门：事业群、业务线、团队
    cities: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    posting_count: Mapped[int] = mapped_column(Integer, default=1)  # 合并了几条 posting
    conflicts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)  # 字段冲突和裁决
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
    big_company: Mapped[str | None] = mapped_column(String(100))  # 按大厂名单归一后的公司，名单外为空
    llm_call_id: Mapped[int | None] = mapped_column(ForeignKey("llm_calls.id", ondelete="SET NULL"))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at()


class SearchChunk(Base):
    """检索用的块（M4）：岗位的职责和要求、帖子（长帖分段）、面试题。由 jobs 和 posts 生成，随时可重建（search/chunking.py）。
    关键词检索用 pg_search 的 BM25 索引（中文分词，迁移里用 SQL 建，模型里不声明），向量检索用 HNSW。"""

    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_embedding", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)  # 稳定编号：job:12:duty、post:5:q3
    kind: Mapped[str] = mapped_column(String(16))  # job / job_duty / job_req / post / post_part / question
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    post_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    companies: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    header: Mapped[str] = mapped_column(Text)  # 上下文头：公司 ｜ 部门 ｜ 岗位名 ｜ 城市 ｜ 类型
    body: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)  # 头 + 正文：检索和算向量都用它
    content_hash: Mapped[str] = mapped_column(String(32))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    embedded_hash: Mapped[str | None] = mapped_column(String(32))  # 算向量时的正文指纹，和 content_hash 不同就要重算
    embed_model: Mapped[str | None] = mapped_column(String(128))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class MemoryFact(Base):
    """记忆里的一条事实（M5，自己实现）。双时间线：valid_* 是这件事在现实里什么时候成立（"下个月起每周能去 4 天"），
    recorded_at / superseded_at 是北极星什么时候知道、什么时候被新说法取代。旧版本不删，能回答"我上个月是怎么说的"。"""

    __tablename__ = "memory_facts"
    __table_args__ = (Index("ix_memory_facts_slot", "attribute", "qualifier", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    attribute: Mapped[str] = mapped_column(String(32))  # 属性，见 memory/schema.py，如 target_city、days_per_week
    qualifier: Mapped[str | None] = mapped_column(String(100))  # 槽位限定，如面试进度按公司分槽："字节跳动"
    value: Mapped[str] = mapped_column(Text)
    value_key: Mapped[str] = mapped_column(String(200))  # 归一后的值，用来判断"是不是同一个说法"
    statement: Mapped[str] = mapped_column(Text)  # 一句话说清这条事实，卡片里显示它
    kind: Mapped[str] = mapped_column(String(16))  # stated 你亲口说的 / inferred 北极星推断的
    status: Mapped[str] = mapped_column(String(16))  # active / pending 待确认 / superseded 被取代 / retracted 作废
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 你最近一次又提到它
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("memory_facts.id", ondelete="SET NULL"))
    source_text: Mapped[str] = mapped_column(Text)  # 从哪句话来的
    thread_id: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()


class MemoryEvent(Base):
    """每一次记忆更新的决定都记一条：新增、更新、作废、不变、待确认、追问、被抑制、确认、拒绝、忘记。可审计、可评测。"""

    __tablename__ = "memory_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    op: Mapped[str] = mapped_column(String(16))
    attribute: Mapped[str] = mapped_column(String(32))
    qualifier: Mapped[str | None] = mapped_column(String(100))
    value: Mapped[str | None] = mapped_column(Text)
    fact_id: Mapped[int | None] = mapped_column(ForeignKey("memory_facts.id", ondelete="SET NULL"))
    old_fact_id: Mapped[int | None] = mapped_column(ForeignKey("memory_facts.id", ondelete="SET NULL"))
    reason: Mapped[str] = mapped_column(Text)
    source_text: Mapped[str | None] = mapped_column(Text)
    thread_id: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemorySuppression(Base):
    """你否认过的推断：同样的推断以后不再提（参考 WeKnora 的抑制记录）。"""

    __tablename__ = "memory_suppressions"
    __table_args__ = (UniqueConstraint("attribute", "value_key", name="uq_memory_suppressions_attr_value"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    attribute: Mapped[str] = mapped_column(String(32))
    value_key: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class Notification(Base):
    """主动决策的每一个决定（M6）：推给你的、放进简报的、不提的都记下，连同分数和理由。
    推过、放进过简报的不会再提（新颖度）；你的反馈（有用 / 关掉）会让同公司、同方向的推送降一档（克制）。"""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_item", "item_key"), Index("ix_notifications_created", "created_at"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_key: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(8))  # job / post
    cite: Mapped[str] = mapped_column(String(32))  # J-12 / P-3
    title: Mapped[str] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(String(200))
    directions: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    channel: Mapped[str] = mapped_column(String(8))  # push / brief / drop
    score: Mapped[float] = mapped_column(Numeric(5, 3))
    match: Mapped[float] = mapped_column(Numeric(5, 3))
    novelty: Mapped[float] = mapped_column(Numeric(5, 3))
    urgency: Mapped[float] = mapped_column(Numeric(5, 3))
    reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    simhash: Mapped[int | None] = mapped_column(BigInteger)
    level: Mapped[str] = mapped_column(String(8))  # 当时的主动程度
    feedback: Mapped[str | None] = mapped_column(String(16))  # useful / dismissed
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
