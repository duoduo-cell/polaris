"""采集器统一接口（PLAN 第 4 节）。M1 只定义，M2 起实现具体的采集器。

两种采集器对应 PLAN 第 4.1 节的第 2、3 步：
- search：搜索型来源（Tavily、Boss、小红书），按查询词搜全平台，结果里自带各家公司
- site：官网型来源（大厂官网、Greenhouse 等），一个站就是一家公司，不按标题过滤，整批拉下实习岗位

A 档在云端跑，B 档在用户本机跑后推到 /api/ingest/raw-items，后面的抽取、对齐、匹配完全共用。
"""
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import ClassVar, Literal, Protocol

from polaris.ingest import RawItemIn


@dataclass
class Query:
    keywords: list[str] = field(default_factory=list)  # search 型用；site 型忽略
    city: str | None = None
    max_pages: int = 3  # B 档低频、只取前几页


class Collector(Protocol):
    name: ClassVar[str]
    tier: ClassVar[Literal["A", "B"]]
    mode: ClassVar[Literal["search", "site"]]

    def collect(self, query: Query) -> Iterable[RawItemIn]: ...
