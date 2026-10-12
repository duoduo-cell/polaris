"""查询词：按"方向 × 大厂 × 帖子类型"生成，存在 search_queries 表里，每轮挑最久没搜过的那批。

一次小红书搜索要 48 秒左右，所以不追求一轮搜完，而是轮换着搜，每个词都会轮到。
"""
import tomllib
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from polaris.companies import load as load_companies
from polaris.config import ROOT
from polaris.db.models import SearchQuery
from polaris.db.session import session_scope

DIRECTIONS_FILE = ROOT / "config" / "directions.toml"
SHORT_NAMES = {"字节跳动": "字节", "阿里巴巴": "阿里", "蚂蚁集团": "蚂蚁", "哔哩哔哩": "B站", "深度求索": "DeepSeek",
               "月之暗面": "Kimi"}


@lru_cache
def directions_config() -> dict:
    return tomllib.loads(DIRECTIONS_FILE.read_text(encoding="utf-8"))


def short_name(name: str) -> str:
    return SHORT_NAMES.get(name, name)


def seed_queries(source: str) -> list[tuple[str, int]]:
    """返回 (查询词, 优先级)。优先级高的先搜。"""
    cfg = directions_config()
    directions, post_types, top = cfg["directions"], cfg["post_types"], cfg["priority_companies"]
    out: list[tuple[str, int]] = []
    if source == "xhs":
        out += [(f"{d} {t}", 3) for d in directions for t in post_types]  # 不限公司的泛搜，最先轮到
        for company in load_companies():
            name = short_name(company.name)
            priority = 2 if name in top else 1
            out += [(f"{name} {d} {t}", priority) for d in directions[:2] for t in post_types]
    elif source == "tencent":
        out += [(f"{d} 实习", 2) for d in directions] + [(d, 1) for d in directions]
    elif source in ("bytedance", "xhs_careers"):  # 官网整批拉：方向词就够，官网本身只有这一家
        out += [(d, 2) for d in directions]
    elif source == "boss":
        out += [(f"{d} 实习", 2) for d in directions]
    elif source == "maimai":  # 职言搜索：公司 × 实习、面经、内推；大厂名单里靠前的先搜
        for company in load_companies():
            name = short_name(company.name)
            priority = 2 if name in top else 1
            out += [(f"{name} {t}", priority) for t in ("实习", "面经", "内推")]
        out += [(f"{d} 面经", 1) for d in directions[:3]]
    out = [(q, 4) for q in cfg.get(f"peer_queries_{source}", [])] + out  # 对着"人和结果"的词，优先级最高（10-12）
    seen: set[str] = set()
    return [(q, p) for q, p in out if not (q in seen or seen.add(q))]


def ensure_seeded(source: str) -> int:
    rows = [{"source": source, "query": q, "origin": "seed", "priority": p} for q, p in seed_queries(source)]
    if not rows:
        return 0
    with session_scope() as s:
        result = s.execute(insert(SearchQuery).values(rows).on_conflict_do_nothing(
            constraint="uq_search_queries_source_query").returning(SearchQuery.id))
        return len(result.all())


def existing(source: str) -> set[str]:
    with session_scope() as s:
        return set(s.execute(select(SearchQuery.query).where(SearchQuery.source == source)).scalars())


def add_discovered(source: str, queries: list[str], priority: int = 2) -> list[str]:
    """把从采集结果里发现的新查询词加进轮换（origin=discovered），已有的跳过。返回真正新加的。"""
    rows = [{"source": source, "query": x, "origin": "discovered", "priority": priority} for x in dict.fromkeys(queries)]
    if not rows:
        return []
    with session_scope() as s:
        result = s.execute(insert(SearchQuery).values(rows).on_conflict_do_nothing(
            constraint="uq_search_queries_source_query").returning(SearchQuery.query))
        return list(result.scalars())


def pick(source: str, n: int) -> list[str]:
    """先挑从没搜过的、优先级高的，再挑最久没搜过的。"""
    with session_scope() as s:
        stmt = (select(SearchQuery.query).where(SearchQuery.source == source)
                .order_by(SearchQuery.last_run_at.asc().nulls_first(), SearchQuery.priority.desc(), SearchQuery.id)
                .limit(n))
        return list(s.execute(stmt).scalars())


def mark(source: str, query: str, new_items: int) -> None:
    with session_scope() as s:
        s.execute(update(SearchQuery).where(SearchQuery.source == source, SearchQuery.query == query).values(
            runs=SearchQuery.runs + 1, new_items=SearchQuery.new_items + new_items, last_run_at=datetime.now(UTC)))
