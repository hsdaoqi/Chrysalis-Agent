"""Chrysalis LLM 模块。

纯 requests 实现，支持 OpenAI 和 Anthropic 协议，流式输出，原生 function calling，
上下文自动裁剪，多模型 failover。
"""

from chrysalis.llm.client import LLMClient
from chrysalis.llm.context import compress_history_tags, trim_messages_history
from chrysalis.llm.failover import FailoverSession
from chrysalis.llm.session import BaseSession
from chrysalis.llm.types import Response, SessionConfig, ToolCall, Usage
from chrysalis.llm.usage import UsageTracker

__all__ = [
    "BaseSession",
    "FailoverSession",
    "LLMClient",
    "Response",
    "SessionConfig",
    "ToolCall",
    "Usage",
    "UsageTracker",
    "compress_history_tags",
    "trim_messages_history",
]


from typing import Callable


def create_session(config: SessionConfig) -> BaseSession:
    """从配置创建一个 LLM 会话。"""
    return BaseSession(config)


def create_client(
    configs: list[SessionConfig] | SessionConfig,
    tracker: UsageTracker | None = None,
    on_history_changed: Callable[[list[dict]], None] | None = None,
) -> LLMClient:
    """便捷工厂：单配置创建普通 client，多配置创建 failover client。"""
    if isinstance(configs, SessionConfig):
        return LLMClient(BaseSession(configs), tracker=tracker, on_history_changed=on_history_changed)
    sessions = [BaseSession(c) for c in configs]
    if len(sessions) == 1:
        return LLMClient(sessions[0], tracker=tracker, on_history_changed=on_history_changed)
    return LLMClient(FailoverSession(sessions), tracker=tracker, on_history_changed=on_history_changed)
