"""Neo4j 连接。图谱是可关的增强：没配 NEO4J_PASSWORD 就当没有，检索只走关键词和向量两路。"""
from functools import lru_cache

from neo4j import Driver, GraphDatabase

from polaris.config import get_settings


def enabled() -> bool:
    pw = get_settings().neo4j_password
    return bool(pw and pw.get_secret_value())


@lru_cache
def driver() -> Driver:
    s = get_settings()
    if not enabled():
        raise RuntimeError("没有配置 NEO4J_PASSWORD，知识图谱未启用")
    return GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password.get_secret_value()),
                                connection_timeout=5)
