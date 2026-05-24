"""LLM 会话管理：history 持久化、上下文裁剪、流式调用生命周期。
history 内部统一使用 canonical block 格式（见 chrysalis/llm/types.py），发送给 provider 时由 protocols 模块转换为协议特定的 wire format。"""

from __future__ import annotations

import threading
from typing import Generator

from chrysalis.llm.claude_stream import claude_stream
from chrysalis.llm.context import trim_messages_history
from chrysalis.llm.openai_stream import openai_stream
from chrysalis.llm.protocols import to_anthropic_messages, to_openai_messages
from chrysalis.llm.types import CancelledError, Response, SessionConfig


class BaseSession:
    """单个 LLM 会话。管理 history、上下文裁剪、协议分发。"""

    def __init__(self, config: SessionConfig):
        self.config = config
        self.history: list[dict] = []
        self.system: str = ""
        self.tools: list[dict] | None = None
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()

    def ask(self, message: dict, cancel_event: threading.Event | None = None) -> Generator[str, None, Response]:
        """追加 canonical message 到 history，流式调用 LLM，返回 Response。"""
        with self._lock:
            self.history.append(message)
            trim_messages_history(self.history, self.config.context_window)
            history_snapshot = [dict(m) for m in self.history]

        cancel = cancel_event or self._cancel_event
        gen = self._raw_ask(history_snapshot, cancel)
        response: Response | None = None
        try:
            while True:
                chunk = next(gen)
                yield chunk
                if cancel.is_set():
                    raise CancelledError()
        except CancelledError:
            response = Response(content="", raw="", stop_reason="cancelled", cancelled=True)
        except StopIteration as e:
            response = e.value

        if response is None:
            response = Response(content="!!!Error: 未收到响应", raw="")

        if not response.is_error and not response.cancelled:
            self._append_assistant(response)

        return response

    def _raw_ask(self, history: list[dict], cancel_event: threading.Event | None = None) -> Generator[str, None, Response]:
        """根据协议把 canonical history 转为 wire format 后分发到对应 stream。"""
        if self.config.protocol == "anthropic":
            messages = to_anthropic_messages(history)
            return claude_stream(self.config, messages, self.system, self.tools, cancel_event=cancel_event)
        messages = to_openai_messages(history, self.system)
        return openai_stream(self.config, messages, "", self.tools, cancel_event=cancel_event)

    def _append_assistant(self, response: Response) -> None:
        """将 assistant 响应追加为 canonical message。"""
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
        self._cancel_event.clear()

    def cancel(self) -> None:
        self._cancel_event.set()
