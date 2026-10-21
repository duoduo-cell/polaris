"""派生知识写入的事务保护；所有入口先取此锁，再取行锁。

不保护未参与协议的原文写入，也不跨模型／网络调用持锁。
调用方拥有事务，异常须回滚；本模块不提交或刷新 ORM 对象。
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

KNOWLEDGE_WRITE_LOCK = 2026102103


class KnowledgeWriteBusy(RuntimeError):
    """另一派生写入尚未结束；重新读取后重试，不代表业务处理失败。"""


def acquire_knowledge_write(s: Session) -> None:
    # Session.scalar 会触发 autoflush；竞争失败前不能先写 caller 的脏对象。
    with s.no_autoflush:
        acquired = s.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": KNOWLEDGE_WRITE_LOCK})
    if not acquired:
        raise KnowledgeWriteBusy("knowledge writer busy; retry in a new transaction")
