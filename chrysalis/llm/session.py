"""LLM 会话管理：history 持久化、上下文裁剪、流式调用生命周期。
history 内部统一使用 canonical block 格式（见 chrysalis/llm/types.py），发送给 provider 时由 protocols 模块转换为协议特定的 wire format。"""

from __future__ import annotations

import json
import threading
from typing import Generator

from chrysalis.llm.claude_stream import claude_stream
from chrysalis.llm.context import (
    COMPACT_SYSTEM_PROMPT,
    CompactionManager,
    is_context_limit_error,
)
from chrysalis.llm.logger import write_llm_log
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
        self.compaction = CompactionManager(config)
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()

    def ask(self, message: dict, cancel_event: threading.Event | None = None) -> Generator[str, None, Response]:
        """追加 canonical message 到 history，流式调用 LLM，返回 Response。"""
        cancel = cancel_event or self._cancel_event
        with self._lock:
            self.history.append(message)
            self.compaction.apply_preflight(self.history, system=self.system, tools=self.tools)
            llm_summary_request = (
                self.compaction.build_llm_summary_request(self.history)
                if self.compaction.should_try_llm_summary(self.history, system=self.system, tools=self.tools)
                else None
            )
            history_snapshot = [dict(m) for m in self.history]

        if llm_summary_request:
            summary = self._run_compaction_summary(llm_summary_request, cancel)
            with self._lock:
                if summary:
                    self.compaction.apply_llm_summary(self.history, summary)
                else:
                    self.compaction.mark_llm_summary_failed()
                self.compaction.apply_preflight(self.history, system=self.system, tools=self.tools)
                history_snapshot = [dict(m) for m in self.history]

        response = yield from self._ask_with_reactive_retry(history_snapshot, cancel)
        if response is None:
            response = Response(content="!!!Error: 未收到响应", raw="")

        if not response.is_error and not response.cancelled:
            self._append_assistant(response)

        return response

    def _raw_ask(self, history: list[dict], cancel_event: threading.Event | None = None) -> Generator[
        str, None, Response]:
        """根据协议把 canonical history 转为 wire format 后分发到对应 stream。"""
        return self._raw_ask_with_options(history, self.system, self.tools, cancel_event=cancel_event)

    def _raw_ask_with_options(
            self,
            history: list[dict],
            system: str,
            tools: list[dict] | None,
            cancel_event: threading.Event | None = None,
    ) -> Generator[str, None, Response]:
        """根据协议把 canonical history 转为 wire format 后分发到对应 stream。"""
        if self.config.protocol == "anthropic":
            messages = to_anthropic_messages(history)
            return claude_stream(self.config, messages, system, tools, cancel_event=cancel_event)
        messages = to_openai_messages(history, system)
        return openai_stream(self.config, messages, "", tools, cancel_event=cancel_event)

    def _ask_with_reactive_retry(
            self,
            history_snapshot: list[dict],
            cancel: threading.Event,
    ) -> Generator[str, None, Response]:
        response = yield from self._stream_raw(history_snapshot, cancel)
        if response and response.cancelled:
            return response
        if not is_context_limit_error(response):
            return response

        with self._lock:
            self.compaction.apply_reactive_compact(self.history)
            retry_snapshot = [dict(m) for m in self.history]

        yield "\n[Chrysalis] 上下文过长，已自动压缩历史并重试一次。\n"
        retry_response = yield from self._stream_raw(retry_snapshot, cancel)
        if is_context_limit_error(retry_response):
            return Response(
                content="!!!Error: 上下文压缩后仍超过模型限制，请开启新会话或缩小任务范围。",
                raw=(retry_response.raw if retry_response else ""),
            )
        return retry_response

    def _stream_raw(
            self,
            history: list[dict],
            cancel: threading.Event,
    ) -> Generator[str, None, Response]:
        gen = self._raw_ask(history, cancel)
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
        return response or Response(content="!!!Error: 未收到响应", raw="")

    def _run_compaction_summary(self, request: list[dict], cancel: threading.Event) -> str:
        gen = self._raw_ask_with_options(request, COMPACT_SYSTEM_PROMPT, None, cancel_event=cancel)
        response: Response | None = None
        try:
            while True:
                next(gen)
                if cancel.is_set():
                    raise CancelledError()
        except CancelledError:
            return ""
        except StopIteration as e:
            response = e.value
        if response is None or response.cancelled or response.is_error:
            return ""
        return response.content.strip()

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
