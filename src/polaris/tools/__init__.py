from polaris.tools import jobs, raw_items
from polaris.tools.base import ToolError, ToolSpec

REGISTRY: dict[str, ToolSpec] = {spec.name: spec for spec in [*raw_items.TOOLS, *jobs.TOOLS]}

__all__ = ["REGISTRY", "ToolError", "ToolSpec"]
