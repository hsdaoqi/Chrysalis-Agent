"""LLM 会话管理：history 持久化、上下文裁剪、流式调用生命周期。

history 内部统一使用 canonical block 格式（见 chrysalis/llm/types.py）。
发送给 provider 时由 protocols 模块转换为协议特定的 wire format。
"""

import threading
from typing import Generator

from chrysalis.llm.claude_stream import claude_stream
from chrysalis.llm.context import trim_messages_history
from chrysalis.llm.openai_stream import openai_stream
from chrysalis.llm.protocols import to_anthropic_messages, to_openai_messages
from chrysalis.llm.types import Response, SessionConfig


class BaseSession:
    """单个 LLM 会话。管理 history、上下文裁剪、协议分发。

    调用方式：
        gen = session.ask(message)
        for chunk in gen:  # yield 文本块
            print(chunk, end="")
        # 实际取 Response 需 try/except StopIteration 或 yield from

    history 中每条消息为 canonical 格式：{"role", "blocks", "_compressed"}。
    """

    def __init__(self, config: SessionConfig):
        self.config = config
        self.history: list[dict] = []  # 发给LLM的历史内容
        self.system: str = ""  # 系统提示词
        self.tools: list[dict] | None = None  # 工具提示词
        self._lock = threading.Lock()

    def ask(self, message: dict) -> Generator[str, None, Response]:
        """追加 canonical message 到 history，流式调用 LLM，返回 Response。"""
        with self._lock:
            self.history.append(message)
            trim_messages_history(self.history, self.config.context_window)
            history_snapshot = [dict(m) for m in self.history]

        gen = self._raw_ask(history_snapshot)
        response: Response | None = None
        try:
            while True:
                chunk = next(gen)
                yield chunk
        except StopIteration as e:
            response = e.value

        if response is None:
            response = Response(content="!!!Error: 未收到响应", raw="")

        if not response.is_error:
            self._append_assistant(response)

        return response

    def _raw_ask(self, history: list[dict]) -> Generator[str, None, Response]:
        """根据协议把 canonical history 转 wire format 后分发到对应 stream。"""
        if self.config.protocol == "anthropic":
            messages = to_anthropic_messages(history)
            return claude_stream(self.config, messages, self.system, self.tools)
        messages = to_openai_messages(history, self.system)
        return openai_stream(self.config, messages, "", self.tools)

    def _append_assistant(self, response: Response) -> None:
        """将 assistant 响应作为 canonical message 追加到 history。"""
        blocks: list[dict] = []
        if response.thinking and response.thinking_signature:
            blocks.append({
                "type": "thinking",
                "text": response.thinking,
                "signature": response.thinking_signature,
            })
        if response.content:
            blocks.append({"type": "text", "text": response.content})
        for tc in response.tool_calls:
            blocks.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments,
            })
        if not blocks:
            return
        with self._lock:
            self.history.append({"role": "assistant", "blocks": blocks})

    def clear_history(self) -> None:
        with self._lock:
            self.history.clear()
