"""任务的模型上下文与正式聊天交付边界；不删除历史检查点或重放工具。"""
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse
from langchain_core.messages import AIMessage
from sqlalchemy import select

from polaris.db.models import ChatMessage, ChatTask
from polaris.db.session import session_scope

TASK_ID = "polaris_task_id"
INTERNAL = "polaris_internal"
UNDELIVERED = "（内部状态：这次回答未正式交付，不能视为用户已收到的推荐或建议。已返回的工具记录只是历史查询证据。）"


class DeliveryUnconfirmed(RuntimeError):
    def __init__(self):
        super().__init__("无法确认先前任务的交付状态，本次未继续调用回答模型；请重新连接核实。")


def tag(message: Any, task_id: str | None) -> Any:
    if not task_id or not isinstance(message, AIMessage):
        return message
    return message.model_copy(update={"additional_kwargs": {**message.additional_kwargs, TASK_ID: task_id}})


def _tag_response(response: Any, task_id: str) -> Any:
    if isinstance(response, AIMessage):
        return tag(response, task_id)
    if isinstance(response, ModelResponse):
        return replace(response, result=[tag(message, task_id) for message in response.result])
    if isinstance(response, ExtendedModelResponse):
        return replace(response, model_response=_tag_response(response.model_response, task_id))
    raise DeliveryUnconfirmed()


def _delivered(task_ids: set[str], thread_id: str | None) -> set[str]:
    try:
        if not thread_id:
            raise DeliveryUnconfirmed()
        with session_scope() as session:
            tasks = session.scalars(select(ChatTask).where(ChatTask.id.in_(task_ids), ChatTask.thread_id == thread_id)).all()
            if {task.id for task in tasks} != task_ids or any(task.state in ("queued", "running") for task in tasks):
                raise DeliveryUnconfirmed()
            # 正式聊天记录是交付依据，不能只凭生成结果或completed字样。
            messages = session.scalars(select(ChatMessage).where(
                ChatMessage.thread_id == thread_id, ChatMessage.role == "assistant",
                ChatMessage.source_key.in_([f"{task_id}:assistant" for task_id in task_ids]))).all()
            return {message.source_key.removesuffix(":assistant") for message in messages if message.content}
    except Exception as exc:
        raise DeliveryUnconfirmed() from exc


class TaskDelivery(AgentMiddleware):
    def __init__(self, task_id: str | None, thread_id: str | None):
        self.task_id, self.thread_id = task_id, thread_id

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        task_ids = {m.additional_kwargs[TASK_ID] for m in messages if isinstance(m, AIMessage)
                    and isinstance(m.additional_kwargs.get(TASK_ID), str) and m.additional_kwargs[TASK_ID] != self.task_id}
        if not task_ids:
            return None
        withheld = task_ids - _delivered(task_ids, self.thread_id)
        updates, owners = [], {}
        for message in messages:
            if isinstance(message, AIMessage):
                owner = message.additional_kwargs.get(TASK_ID)
                for call in message.tool_calls:
                    owners[call["id"]] = (owner, call["name"])
                content = "" if message.tool_calls else UNDELIVERED
                redact = owner in withheld
            elif message.type == "tool":
                owner, name = owners.get(message.tool_call_id, (None, None))
                redact, content = owner in withheld and name == "career_report", UNDELIVERED
            else:
                continue
            if redact and (message.content != content or not message.additional_kwargs.get(INTERNAL)):
                updates.append(message.model_copy(update={
                    "content": content, "additional_kwargs": {**message.additional_kwargs, INTERNAL: True}}))
        return {"messages": updates} if updates else None

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        response = handler(request)
        # 在模型节点落检查点之前标记；after_model会留下“节点已写入、标记尚未执行”的停止窗口。
        return _tag_response(response, self.task_id) if self.task_id else response
