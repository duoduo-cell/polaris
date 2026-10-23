"""演示只查合成标记；个人模式仍默认排除合成，不用清除标记绕过过滤。"""
from typing import Any

from polaris.config import get_settings


def synthetic_filter(column: Any) -> Any:
    return column.is_(get_settings().demo_mode)


def demo_tool_allowed(name: str, *, live: bool = False) -> bool:
    return not get_settings().demo_mode or (not live and name not in ("kg_stats", "platform_search", "source_open"))
