"""一轮采集：挑查询词 → 逐个搜 → 采到一条写一条 → 记下哪些词真搜过。命令行和侦察子 agent 共用。"""
from collections.abc import Callable
from typing import Any

from polaris.collectors import queries as q
from polaris.collectors.base import Query
from polaris.companies import is_big
from polaris.ingest import ingest_raw_items
from polaris.runs import run_scope


def collect_round(source: str, collector: Any, n_queries: int, params: dict[str, Any],
                  echo: Callable[[str], None] = lambda _: None) -> dict[str, Any]:
    """返回这一轮的统计，含 run_id 和新写入的原始条目编号 new_ids。"""
    q.ensure_seeded(source)
    batch = q.pick(source, n_queries)
    new_ids: list[int] = []
    with run_scope("collect", {"source": source, "queries": batch, **params}) as (run_id, stats):
        inserted = got = big = 0
        if hasattr(collector, "per_query_calls") and params.get("max_calls"):
            collector.per_query_calls = max(2, params["max_calls"] // max(1, len(batch)))
        searched: list[str] = []

        def record() -> None:  # 中途被打断（Ctrl+C、出错）也把已经用掉的调用数记下，第二天的量才算得对
            extra = vars(collector.stats) if hasattr(collector, "stats") else {}
            stats.update({**extra, "items": got, "inserted": inserted, "big_company_items": big,
                          "searched_queries": searched})

        try:
            for kw in batch:
                n_kw = new_kw = 0
                searched_before = getattr(getattr(collector, "stats", None), "searched", None)
                # 采到一条写一条：中途中断也不丢已经采到的
                for item in collector.collect(Query(keywords=[kw], max_pages=params.get("max_pages", 1))):
                    n_kw += 1
                    big += is_big(item.raw_text)
                    result = ingest_raw_items([item], run_id)
                    new_kw += result.inserted
                    inserted += result.inserted
                    got += 1
                    new_ids += result.ids  # 只含新写入的
                searched_after = getattr(getattr(collector, "stats", None), "searched", None)
                if searched_before is None or searched_after != searched_before:  # 真搜过才记，没轮到的下次优先
                    q.mark(source, kw, new_kw)
                    searched.append(kw)
                    echo(f"  「{kw}」采到 {n_kw} 条，新写入 {new_kw} 条")
                else:
                    echo(f"  「{kw}」本轮预算用完，没搜，下次优先")
        finally:
            record()
        extra = vars(collector.stats) if hasattr(collector, "stats") else {}
        out = {**stats, "run_id": run_id, "new_ids": new_ids}
    echo(f"本轮：采到 {got} 条，新写入 {inserted} 条，其中提到大厂的 {big} 条。")
    if extra.get("errors"):
        echo(f"失败 {extra.get('failures')} 次，例如：{extra['errors'][0]}")
    return out
