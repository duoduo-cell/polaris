"""工具层约定。依据 Anthropic《Writing effective tools for agents》（见 docs/INTRO.md [T1]）：

- 命名：命名空间前缀加动作，如 raw_items_search；同一组工具前缀相同，方便按组启用或屏蔽
- 返回：response_format 取 concise（只给高信号字段）或 detailed（再给技术编号、时间等）
- 分页：limit 默认 10、最多 50，加 offset；结果太长时截断，并提示怎么缩小范围
- 错误：抛 ToolError(问题, 怎么改, 示例)，模型看到的是能照着改的建议，不是错误码
- 编号：用短编号（如 R-12），不用 UUID
- 副作用：read_only=False 的工具（发消息、写库等）必须经人工审批，M6 接入
"""
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

MAX_OUTPUT_CHARS = 6000
TRUNCATED_HINT = "（结果太长，后面的已截断。请用更具体的查询词，或把 limit 调小再用 offset 翻页。）"


class ToolError(Exception):
    """工具能预料到的错误。render() 的内容会原样交给模型，所以要写成它能照着改的建议。"""

    def __init__(self, problem: str, fix: str, example: str | None = None):
        super().__init__(problem)
        self.problem, self.fix, self.example = problem, fix, example

    def render(self) -> str:
        text = f"出错了：{self.problem}\n怎么改：{self.fix}"
        return text + (f"\n示例：{self.example}" if self.example else "")


class Page(BaseModel):
    """所有列表类工具共用的分页和返回格式参数。"""

    limit: int = Field(10, ge=1, le=50, description="每页条数，1 到 50")
    offset: int = Field(0, ge=0, description="从第几条开始，翻页时用上一页返回的 next_offset")
    response_format: Literal["concise", "detailed"] = Field(
        "concise", description="concise 只给关键信息；需要链接、时间、编号等细节时用 detailed")


@dataclass
class ToolSpec:
    name: str
    description: str
    args_schema: type[BaseModel]
    func: Callable[[Any], Any]
    read_only: bool = True
    example: str | None = None  # 一个正确的参数示例，参数出错时给模型看
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def namespace(self) -> str:
        return self.name.rsplit("_", 1)[0]


def render(data: Any) -> str:
    """工具结果统一转成 JSON 文本；超长时截断并附提示。"""
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str)
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + TRUNCATED_HINT
    return text


def validation_error(spec: ToolSpec, err: ValidationError) -> ToolError:
    problems = "；".join(f"参数 {'.'.join(str(p) for p in e['loc']) or '(整体)'}：{e['msg']}" for e in err.errors()[:4])
    return ToolError(f"参数不合格：{problems}", "按工具说明改正参数后再调用一次", spec.example)


def canonical_args(args: dict[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
