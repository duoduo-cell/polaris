"""避免模型把工具前内部草稿当成用户已看到的回答，不直接拼接草稿。"""
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
    """仅当本轮工具前有未展示文字时，遮去请求里的草稿并给收尾提示。

    不复制外部资料到 system、不追加模型调用、不修改持久对话或直接展示草稿。
    """

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        messages = request.messages
        if not messages or getattr(messages[-1], "type", "") != "tool":
            return handler(request)
        start = max((i for i, message in enumerate(messages) if getattr(message, "type", "") == "human"), default=-1)
        hidden = {i for i, message in enumerate(messages) if i > start
                  and getattr(message, "type", "") == "ai" and getattr(message, "tool_calls", None)
                  and text_of(message.content).strip()}
        if start >= 0 and hidden:
            # 只改变本次发给模型的视图。工具调用/ID、证据、旧轮回答和持久检查点都保留。
            visible = [message.model_copy(update={"content": ""}) if i in hidden else message
                       for i, message in enumerate(messages)]
            return handler(request.override(messages=[*visible, SystemMessage(content=FINAL_ANSWER_NOTE)]))
        return handler(request)
