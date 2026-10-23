"""避免模型把工具前内部草稿当成用户已看到的回答，不直接拼接草稿。"""
import json
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
    "时间字段口径：fetched_at是该原文版本的保存时刻，不自动等于岗位第一次采集；"
    "last_seen是再次识别到记录的日期，不证明完整页面可读、未下架或仍在招聘。"
    "面经未标团队应写团队未知，不能断言同团队或不是同团队；建议与面经原题分别标明。"
)


def _search_count_note(messages: list[Any]) -> str:
    """只提升已知工具的数字元数据；不把原文、城市或自由文本变成系统指令。"""
    for message in reversed(messages):
        if (getattr(message, "type", "") != "tool" or getattr(message, "name", "") != "jobs_search"
                or getattr(message, "status", "success") == "error"):
            continue
        try:
            data = json.loads(text_of(message.content))
        except (ValueError, TypeError):
            return ""
        if not isinstance(data, dict):
            return ""
        page, items = data.get("pagination"), data.get("items")
        if not isinstance(page, dict) or not isinstance(items, list):
            return ""
        count = page.get("candidate_count")
        if type(count) is not int or not 0 <= count <= 100_000_000:
            return ""
        next_offset = page.get("next_offset")
        paging = ("还有下一页。" if type(next_offset) is int and next_offset >= 0 else
                  "没有下一页。" if "next_offset" in page and next_offset is None else "下一页信息未知。")
        note = (f"本轮最近一次成功的岗位搜索：候选{count}，当前仅展示{len(items)}，"
                + paging
                + "展示数不是符合条件的总数；候选数也不证明个人资格全部符合。")
        days = data.get("days_filter")
        unknown = days.get("jd_without_days") if isinstance(days, dict) else None
        if type(unknown) is int and 0 <= unknown <= 100_000_000:
            note += f"天数筛选前更宽范围有{unknown}条未写天数，不是上述候选内部的分组，不能混用分母。"
        return "\n" + note
    return ""


class FinalAnswerReminder(AgentMiddleware):
    """本轮工具返回后提醒完整收尾，有未展示文字时另外遮去请求里的草稿。

    只把数字计数元数据及固定口径放到system；不提升外部文本、不追加调用、不修改持久对话。
    """

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        messages = request.messages
        if not messages or getattr(messages[-1], "type", "") != "tool":
            return handler(request)
        start = max((i for i, message in enumerate(messages) if getattr(message, "type", "") == "human"), default=-1)
        hidden = {i for i, message in enumerate(messages) if i > start
                  and getattr(message, "type", "") == "ai" and getattr(message, "tool_calls", None)
                  and text_of(message.content).strip()}
        if start >= 0:
            # 只改变本次发给模型的视图。工具调用/ID、证据、旧轮回答和持久检查点都保留。
            visible = [message.model_copy(update={"content": ""}) if i in hidden else message
                       for i, message in enumerate(messages)]
            note = FINAL_ANSWER_NOTE + _search_count_note(messages[start + 1:])
            return handler(request.override(messages=[*visible, SystemMessage(content=note)]))
        return handler(request)
