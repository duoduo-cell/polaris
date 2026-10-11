"""Polaris Core 的 REST 接口。/docs 是自动生成的接口文档，可以直接试调。"""
import hmac
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from polaris import __version__
from polaris.config import get_settings
from polaris.db.models import LLMCall, RawItem, Run, ToolCall
from polaris.db.session import db_ok, session_factory
from polaris.ingest import IngestResult, RawItemIn, ingest_raw_items
from polaris.mcp_server import build_mcp

mcp = build_mcp()
mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    from polaris.agent.tasks import service, shutdown

    service()
    async with mcp.session_manager.run():
        try:
            yield
        finally:
            shutdown()


app = FastAPI(
    title="Polaris Core",
    version=__version__,
    description=(
        "Business core of Polaris, a personal job-hunting agent for LLM and Agent internships. "
        "Every model call and tool call is traced (tokens, latency, cost) and every job can be traced back "
        "to its source. Read-only tools are also exposed over MCP (streamable HTTP) at /mcp/ with a bearer token."
    ),
    lifespan=lifespan,
)


class BearerGuard:
    """给挂载的 MCP 服务加口令。没配 MCP_TOKEN 时整个 /mcp 关闭。"""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            token = get_settings().mcp_token
            if token is None:
                await JSONResponse({"detail": "MCP is disabled: MCP_TOKEN is not configured"}, 503)(scope, receive, send)
                return
            auth = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(auth, f"Bearer {token.get_secret_value()}".encode()):
                await JSONResponse({"detail": "invalid or missing bearer token"}, 401)(scope, receive, send)
                return
        await self.inner(scope, receive, send)


app.mount("/mcp", BearerGuard(mcp_app))


def get_db() -> Iterator[Session]:
    with session_factory()() as session:
        yield session


Db = Annotated[Session, Depends(get_db)]


def preview(text: str | None, limit: int = 300) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit] + "…"


# ---- 返回结构 ----

class Health(BaseModel):
    status: str
    version: str
    database: bool
    config: dict[str, Any]


class LLMCallSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    run_id: int | None
    purpose: str
    prompt_version: str | None
    provider: str
    model: str
    endpoint: str
    parsed_ok: bool | None
    error: str | None
    attempt: int
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    latency_ms: int
    cost_usd: float | None
    cache_hit: bool
    created_at: datetime


class LLMCallDetail(LLMCallSummary):
    request: dict[str, Any]
    response: dict[str, Any] | None
    cached_tokens: int | None


class ToolCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    run_id: int | None
    thread_id: str | None
    agent: str | None
    tool: str
    args: dict[str, Any]
    output: str | None
    ok: bool
    error_kind: str | None
    latency_ms: int
    replayed: bool
    created_at: datetime


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    params: dict[str, Any]
    status: str
    stats: dict[str, Any]
    git_commit: str | None
    started_at: datetime
    finished_at: datetime | None


class RawItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source: str
    tier: str
    kind: str
    url: str | None
    external_id: str | None
    query: str | None
    raw_text: str
    content_hash: str
    fetched_at: datetime
    run_id: int | None
    is_synthetic: bool


class IngestIn(BaseModel):
    items: list[RawItemIn] = Field(min_length=1, max_length=500)


# ---- 接口 ----

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")


@app.get("/health", response_model=Health, tags=["system"], summary="Service, database and model config")
def health() -> Health:
    ok = db_ok()
    return Health(status="ok" if ok else "degraded", version=__version__, database=ok,
                  config=get_settings().public_summary())


@app.get("/api/llm-calls", response_model=list[LLMCallSummary], tags=["tracing"],
         summary="List traced model calls (newest first)")
def list_llm_calls(db: Db, purpose: str | None = None, model: str | None = None, run_id: int | None = None,
                   limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> list[LLMCall]:
    stmt = select(LLMCall).order_by(LLMCall.id.desc()).limit(limit).offset(offset)
    if purpose:
        stmt = stmt.where(LLMCall.purpose == purpose)
    if model:
        stmt = stmt.where(LLMCall.model == model)
    if run_id is not None:
        stmt = stmt.where(LLMCall.run_id == run_id)
    return list(db.execute(stmt).scalars())


@app.get("/api/llm-calls/{call_id}", response_model=LLMCallDetail, tags=["tracing"],
         summary="One model call with full request and response, for replay")
def get_llm_call(call_id: int, db: Db) -> LLMCall:
    call = db.get(LLMCall, call_id)
    if call is None:
        raise HTTPException(404, "llm call not found")
    return call


@app.get("/api/tool-calls", response_model=list[ToolCallOut], tags=["tracing"],
         summary="List traced tool calls (newest first), including failed and replayed ones")
def list_tool_calls(db: Db, run_id: int | None = None, tool: str | None = None, ok: bool | None = None,
                    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> list[ToolCallOut]:
    stmt = select(ToolCall).order_by(ToolCall.id.desc()).limit(limit).offset(offset)
    if run_id is not None:
        stmt = stmt.where(ToolCall.run_id == run_id)
    if tool:
        stmt = stmt.where(ToolCall.tool == tool)
    if ok is not None:
        stmt = stmt.where(ToolCall.ok.is_(ok))
    out = []
    for call in db.execute(stmt).scalars():
        row = ToolCallOut.model_validate(call)
        row.output = preview(row.output, 500)
        out.append(row)
    return out


@app.get("/api/runs", response_model=list[RunOut], tags=["tracing"],
         summary="List runs (collect, extract, eval, seed, synth) with their metrics")
def list_runs(db: Db, kind: str | None = None, limit: int = Query(50, ge=1, le=200),
              offset: int = Query(0, ge=0)) -> list[Run]:
    stmt = select(Run).order_by(Run.id.desc()).limit(limit).offset(offset)
    if kind:
        stmt = stmt.where(Run.kind == kind)
    return list(db.execute(stmt).scalars())


@app.get("/api/runs/{run_id}", response_model=RunOut, tags=["tracing"], summary="One run")
def get_run(run_id: int, db: Db) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


@app.get("/api/raw-items", response_model=list[RawItemOut], tags=["data"],
         summary="List raw items as collected (text truncated; fetch one item for the full text)")
def list_raw_items(db: Db, source: str | None = None, tier: str | None = None, kind: str | None = None,
                   limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> list[RawItemOut]:
    stmt = select(RawItem).order_by(RawItem.id.desc()).limit(limit).offset(offset)
    if source:
        stmt = stmt.where(RawItem.source == source)
    if tier:
        stmt = stmt.where(RawItem.tier == tier)
    if kind:
        stmt = stmt.where(RawItem.kind == kind)
    out = []
    for item in db.execute(stmt).scalars():
        row = RawItemOut.model_validate(item)
        row.raw_text = preview(row.raw_text) or ""
        out.append(row)
    return out


@app.get("/api/raw-items/{item_id}", response_model=RawItemOut, tags=["data"], summary="One raw item")
def get_raw_item(item_id: int, db: Db) -> RawItem:
    item = db.get(RawItem, item_id)
    if item is None:
        raise HTTPException(404, "raw item not found")
    return item


from polaris.api.personal import router as personal_router  # noqa: E402  放在 app 建好之后

app.include_router(personal_router)


def require_ingest_token(authorization: Annotated[str | None, Header()] = None) -> None:
    token = get_settings().ingest_token
    if token is None:
        raise HTTPException(503, "ingest is disabled: INGEST_TOKEN is not configured")
    expected = f"Bearer {token.get_secret_value()}"
    if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(401, "invalid or missing bearer token")


@app.post("/api/ingest/raw-items", response_model=IngestResult, tags=["data"],
          dependencies=[Depends(require_ingest_token)],
          summary="Push raw items (local collectors, manual paste). Deduplicated by source + content hash")
def ingest(body: IngestIn) -> IngestResult:
    return ingest_raw_items(body.items)
