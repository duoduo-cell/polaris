from polaris.tools import jobs, knowledge, live, memory, peers, raw_items
from polaris.tools.base import ToolError, ToolSpec

REGISTRY: dict[str, ToolSpec] = {spec.name: spec for spec in [*raw_items.TOOLS, *jobs.TOOLS, *knowledge.TOOLS, *memory.TOOLS,
                                                               *live.TOOLS, *peers.TOOLS]}

__all__ = ["REGISTRY", "ToolError", "ToolSpec"]
