from polaris.tools import jobs, knowledge, memory, raw_items
from polaris.tools.base import ToolError, ToolSpec

REGISTRY: dict[str, ToolSpec] = {spec.name: spec for spec in [*raw_items.TOOLS, *jobs.TOOLS, *knowledge.TOOLS, *memory.TOOLS]}

__all__ = ["REGISTRY", "ToolError", "ToolSpec"]
