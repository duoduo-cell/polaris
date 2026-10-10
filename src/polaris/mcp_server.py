"""把 Polaris 的只读工具以 MCP（streamable HTTP）开放出去，Hermes、Claude 等任何 MCP 客户端都能接。

工具定义只有一份（polaris/tools），这里把它转成 MCP 工具，执行仍走 ToolRuntime，所以同样会记账。
有副作用的工具不开放。
"""
import inspect
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP

from polaris.tools import REGISTRY
from polaris.tools.base import ToolSpec
from polaris.tools.runtime import ToolRuntime


def _mcp_function(spec: ToolSpec):
    def run(**kwargs: Any) -> str:
        return ToolRuntime(agent="mcp").invoke(spec, kwargs)

    # 用参数模型的字段生成函数签名，FastMCP 据此生成工具的输入 schema（带说明和取值范围）
    params = [
        inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=Annotated[f.annotation, f],
                          default=inspect.Parameter.empty if f.is_required() else f.default)
        for name, f in spec.args_schema.model_fields.items()
    ]
    run.__signature__ = inspect.Signature(params, return_annotation=str)
    run.__annotations__ = {p.name: p.annotation for p in params} | {"return": str}
    run.__name__ = spec.name
    run.__doc__ = spec.description
    return run


def build_mcp() -> FastMCP:
    mcp = FastMCP("polaris", stateless_http=True, json_response=True)
    mcp.settings.streamable_http_path = "/"
    for spec in REGISTRY.values():
        if spec.read_only:
            mcp.add_tool(_mcp_function(spec), name=spec.name, description=spec.description)
    return mcp
