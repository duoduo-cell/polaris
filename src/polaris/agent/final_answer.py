"""工具后的正式回答应覆盖尚未展示给用户的内容，不直接拼接中间文字。"""
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from polaris.agent.minimal import text_of

FINAL_ANSWER_NOTE = (
    "工具结果已返回。请基于当前证据完整回答最近一条用户消息的全部明确要求。"
    "带工具调用的中间回复没有向用户展示；其中已写出的更正或解释也必须在最终回答中完整交代，"
    "不能只回答最后一个子问题。不要把中间推测当成工具已证实的事实。"
)


class FinalAnswerReminder(AgentMiddleware):
    """仅当本轮工具前有未展示的文字时，在模型请求末尾给可信提示。

    不复制外部资料到 system、不追加模型调用、不修改持久对话或直接展示草稿。
    """

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        messages = request.messages
        if not messages or getattr(messages[-1], "type", "") != "tool":
            return handler(request)
        start = max((i for i, message in enumerate(messages) if getattr(message, "type", "") == "human"), default=-1)
        hidden = any(getattr(message, "type", "") == "ai" and getattr(message, "tool_calls", None)
                     and text_of(message.content).strip() for message in messages[start + 1:])
        if start >= 0 and hidden:
            return handler(request.override(messages=[*messages, SystemMessage(content=FINAL_ANSWER_NOTE)]))
        return handler(request)
