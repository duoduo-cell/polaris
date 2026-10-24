"""避免模型把工具前内部草稿当成用户已看到的回答，不直接拼接草稿。"""
import json
import re
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse
from langchain_core.messages import AIMessage, SystemMessage

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
        scope = page.get("scope_candidate_count")
        search = data.get("search")
        queried = isinstance(search, dict) and search.get("query_executed") is True
        scoped = type(scope) is int and 0 <= scope <= 100_000_000
        counts = f"结构化范围候选{scope}；关键词召回{count}" if scoped and queried else f"候选{count}"
        note = (f"本轮最近一次成功的岗位搜索：{counts}，当前仅展示{len(items)}，"
                + paging
                + "展示数不是符合条件的总数；候选数也不证明个人资格全部符合。")
        if queried:
            note += "未召回不等于不符合；是否还有下一页仅指召回子集，不能据此声称结构化范围只有这些岗位。"
        days = data.get("days_filter")
        unknown = days.get("jd_without_days") if isinstance(days, dict) else None
        if type(unknown) is int and 0 <= unknown <= 100_000_000:
            note += f"结构化范围中有{unknown}条未写天数，已保留并标为待确认，不能说它们符合天数要求。"
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


ASKS_QUESTIONS = re.compile(r"面经|面试题|原题|真题")
QUESTION_TOOLS = ("questions_search", "posts_search", "posts_get", "career_report")
MISSING_QUESTIONS_NOTE = (
    "用户这句话要了面经或面试原题，但目前对话里还没有成功的 questions_search／posts_search 结果，"
    "你正准备直接给最终答复。先调用 questions_search（按所选岗位的公司、方向）取题；"
    "查不到就如实说查了什么、没查到，不能写“库里找到”或自拟题目冒充原题。"
)


def _ai_of(response: Any) -> AIMessage | None:
    if isinstance(response, AIMessage):
        return response
    if isinstance(response, ModelResponse):
        return next((m for m in reversed(response.result) if isinstance(m, AIMessage)), None)
    if isinstance(response, ExtendedModelResponse):
        return _ai_of(response.model_response)
    return None


class QuestionEvidenceGate(AgentMiddleware):
    """用户要了面经原题、模型却没查就要收尾时，退回去让它先查一次（每次模型调用最多一次）。

    10-24 整链实测：同一请求三次里一次没调用 questions_search，回答写“我在库里找到了一道面试题”，题目库里和工具结果里都没有。
    提示词早有“不得编造原题”，模型仍会跳过；这里在编排层兜底，不改变查到之后怎么写。
    """

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        response = handler(request)
        if not request.tools:  # 最后一次调用已经不给工具，只能如实收尾
            return response
        messages = request.messages
        human = next((m for m in reversed(messages) if getattr(m, "type", "") == "human"), None)
        if human is None or not ASKS_QUESTIONS.search(text_of(human.content)):
            return response
        if any(getattr(m, "type", "") == "tool" and getattr(m, "name", "") in QUESTION_TOOLS
               and getattr(m, "status", "success") != "error" for m in messages):
            return response
        ai = _ai_of(response)
        if ai is None or ai.tool_calls:
            return response
        return handler(request.override(messages=[*messages, SystemMessage(content=MISSING_QUESTIONS_NOTE)]))
