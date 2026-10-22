"""agent 用的模型：现场调用的 ChatOpenAI（地址、key、模型名全从 .env 读），以及按录像回放的模型。"""
from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI

from polaris.config import Role, Settings, get_settings
from polaris.replay import Cassette


def chat_model(role: Role = "chat", settings: Settings | None = None, **kwargs: Any) -> ChatOpenAI:
    """重试交给 LangChain 的 ModelRetryMiddleware 做（每次重试都会经过回调被记账），所以这里关掉 SDK 自带的重试。"""
    s = settings or get_settings()
    if s.llm_enable_thinking is not None:
        kwargs = {**kwargs, "extra_body": {"enable_thinking": s.llm_enable_thinking,
                                         **(kwargs.get("extra_body") or {})}}
    return ChatOpenAI(model=s.model_for(role), base_url=s.chat_base, api_key=s.llm_api_key.get_secret_value(),
                      timeout=s.llm_timeout, max_retries=0, **kwargs)


class ReplayChatModel(BaseChatModel):
    """完整回放用：不调任何服务，按录制顺序依次返回录下的回答（包括工具调用）。"""

    cassette: Any
    model_name: str = "replay"

    @property
    def _llm_type(self) -> str:
        return "polaris-replay"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model": self.model_name}

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult:
        recorded = self.cassette.next_llm()
        calls = [{"id": c.get("id") or f"call_{i}", "name": c["name"], "args": c["args"], "type": "tool_call"}
                 for i, c in enumerate(recorded.get("tool_calls") or [])]
        msg = AIMessage(content=recorded.get("content") or "", tool_calls=calls)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ReplayChatModel":
        return self


def replay_model(cassette: Cassette) -> ReplayChatModel:
    return ReplayChatModel(cassette=cassette)
