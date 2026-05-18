"""Chrysalis LLM 模块。

纯 requests 实现，支持 OpenAI 和 Anthropic 协议，流式输出，原生 function calling，
上下文自动裁剪，多模型 failover。
"""

from chrysalis.llm.client import LLMClient
from chrysalis.llm.context import compress_history_tags, trim_messages_history
from chrysalis.llm.failover import FailoverSession
from chrysalis.llm.session import BaseSession
from chrysalis.llm.types import Response, SessionConfig, ToolCall

__all__ = [
    "BaseSession",
    "FailoverSession",
    "LLMClient",
    "Response",
    "SessionConfig",
    "ToolCall",
    "compress_history_tags",
    "trim_messages_history",
]


def create_session(config: SessionConfig) -> BaseSession:
    """从配置创建一个 LLM 会话。"""
    return BaseSession(config)


def create_client(configs: list[SessionConfig] | SessionConfig) -> LLMClient:
    """便捷工厂：单配置创建普通 client，多配置创建 failover client。"""
    if isinstance(configs, SessionConfig):
        return LLMClient(BaseSession(configs))
    sessions = [BaseSession(c) for c in configs]
    if len(sessions) == 1:
        return LLMClient(sessions[0])
    return LLMClient(FailoverSession(sessions))
