"""避免模型把工具前内部草稿当成用户已看到的回答，不直接拼接草稿。"""
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from polaris.agent.minimal import text_of

FINAL_ANSWER_NOTE = (
    "工具结果已返回，但不代表用户要求的资料都已查齐。先核对最近一条用户消息的每项要求："
    "缺少原文、面经或题目证据时，继续使用本次授权范围内的合适工具；查不到如实说明，"
    "不得编造原题、出处或编号。带工具调用的中间文字未向用户展示；准备最终答复时必须从头覆盖全部要求，"
    "不能只接写最后部分。区分工具返回的事实与自行推导的准备建议。"
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
