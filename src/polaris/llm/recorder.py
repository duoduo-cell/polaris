"""把每次模型调用记下来。正式运行写数据库，单元测试用内存版。"""
from typing import Any, Protocol

from sqlalchemy import Engine, select

from polaris.db.models import LLMCall
from polaris.db.session import session_scope


class Recorder(Protocol):
    def record(self, row: dict[str, Any]) -> int | None: ...

    def find_cached(self, cache_key: str) -> dict[str, Any] | None: ...


class DbRecorder:
    def __init__(self, engine: Engine | None = None):
        self.engine = engine

    def record(self, row: dict[str, Any]) -> int:
        with session_scope(self.engine) as session:
            call = LLMCall(**row)
            session.add(call)
            session.flush()
            return call.id

    def find_cached(self, cache_key: str) -> dict[str, Any] | None:
        """同样的请求最近一次成功的回答。校验没通过的不算。"""
        with session_scope(self.engine) as session:
            stmt = (
                select(LLMCall.response)
                .where(LLMCall.cache_key == cache_key, LLMCall.error.is_(None),
                       LLMCall.parsed_ok.is_not(False), LLMCall.response.is_not(None))
                .order_by(LLMCall.id.desc())
                .limit(1)
            )
            return session.execute(stmt).scalar_one_or_none()


class MemoryRecorder:
    def __init__(self):
        self.rows: list[dict[str, Any]] = []

    def record(self, row: dict[str, Any]) -> int:
        self.rows.append(row)
        return len(self.rows)

    def find_cached(self, cache_key: str) -> dict[str, Any] | None:
        for row in reversed(self.rows):
            if (row.get("cache_key") == cache_key and not row.get("error")
                    and row.get("parsed_ok") is not False and row.get("response") is not None):
                return row["response"]
        return None
