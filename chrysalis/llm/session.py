"""LLM 会话管理：history 持久化、上下文裁剪、流式调用生命周期。"""

import threading
from typing import Generator

from chrysalis.llm.claude_stream import claude_stream
from chrysalis.llm.context import trim_messages_history
from chrysalis.llm.openai_stream import openai_stream
from chrysalis.llm.types import Response, SessionConfig, ToolCall


class BaseSession:
    """单个 LLM 会话。管理 history、上下文裁剪、协议分发。

    调用方式：
        gen = session.ask(message)
        for chunk in gen:  # yield 文本块
            print(chunk, end="")
        response = gen.value  # 不可用，需用 try/except StopIteration
    实际使用时通过 exhaust_generator() 或 yield from 获取 Response。
    """

    def __init__(self, config: SessionConfig):
        self.config = config
        self.history: list[dict] = []
        self.system: str = ""
        self.tools: list[dict] | None = None
        self._lock = threading.Lock()

    def ask(self, message: dict) -> Generator[str, None, Response]:
        """追加 message 到 history，流式调用 LLM，返回 Response。

        Generator protocol:
            yield -> str (文本块，用于流式显示)
            return -> Response (通过 StopIteration.value)
        """
        with self._lock:
            self.history.append(message)
            trim_messages_history(self.history, self.config.context_window)
            messages = [dict(m) for m in self.history]

        gen = self._raw_ask(messages)
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

    def _raw_ask(self, messages: list[dict]) -> Generator[str, None, Response]:
        """根据协议分发到对应的流式解析器。"""
        if self.config.protocol == "anthropic":
            return claude_stream(self.config, messages, self.system, self.tools)
        return openai_stream(self.config, messages, self.system, self.tools)

    def _append_assistant(self, response: Response) -> None:
        """将 assistant 响应追加到 history。"""
        with self._lock:
            if response.tool_calls:
                content_blocks = []
                if response.thinking:
                    content_blocks.append({"type": "thinking", "thinking": response.thinking})
                if response.content:
                    content_blocks.append({"type": "text", "text": response.content})
                for tc in response.tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                self.history.append({"role": "assistant", "content": content_blocks})
            else:
                self.history.append({"role": "assistant", "content": response.content})

    def clear_history(self) -> None:
        with self._lock:
            self.history.clear()
