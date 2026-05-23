"""统一 LLM Client：agent_loop 直接调用的接口。

负责：
- 将 agent_loop 传来的 messages（含 tool_results）合并为一条 canonical user message
- 透传流式输出
- 记录原始 prompt/response 日志
- 跟踪 pending tool_use IDs，缺失的工具结果用空字符串补齐（避免协议级断裂）
"""

import json
from typing import Callable, Generator

from chrysalis.llm.logger import write_llm_log
from chrysalis.llm.session import BaseSession
from chrysalis.llm.types import Response, Usage
from chrysalis.llm.usage import UsageTracker


class LLMClient:
    """agent_loop 的唯一 LLM 接口。"""

    def __init__(
            self,
            session: BaseSession,
            tracker: UsageTracker | None = None,
            on_history_changed: Callable[[list[dict]], None] | None = None,
    ):
        self.session = session
        self._pending_tool_ids: list[str] = []
        self.tracker = tracker or UsageTracker()
        self._on_history_changed = on_history_changed

    @property
    def history(self) -> list[dict]:
        return self.session.history

    def set_system(self, system: str) -> None:
        self.session.system = system

    def set_tools(self, tools: list[dict]) -> None:
        self.session.tools = tools

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Generator[str, None, Response]:
        """处理 agent_loop 传来的 messages，调用 LLM，流式返回。

        messages 格式（来自 agent_loop）：
        [{"role": "system", "content": "..."},
         {"role": "user", "content": "...", "tool_results": [...]}]

        Generator protocol:
            yield -> str (文本块)
            return -> Response
        """
        if tools is not None:
            self.session.tools = tools

        for msg in messages:
            if msg.get("role") == "system":
                self.session.system = msg["content"]

        canonical_message = self._merge_user_message(messages)
        write_llm_log("Prompt", json.dumps(canonical_message, ensure_ascii=False, indent=2, default=str))

        gen = self.session.ask(canonical_message)
        response: Response | None = None
        try:
            while True:
                chunk = next(gen)
                yield chunk
        except StopIteration as e:
            response = e.value

        if response is None:
            response = Response(content="!!!Error: 未收到响应", raw="")

        self.tracker.record_turn(response.usage)

        write_llm_log("Response", response.raw or response.content)

        if response.tool_calls:
            self._pending_tool_ids = [tc.id for tc in response.tool_calls]
        else:
            self._pending_tool_ids = []

        if self._on_history_changed:
            self._on_history_changed(self.session.history)

        return response

    def _merge_user_message(self, messages: list[dict]) -> dict:
        """将 agent_loop 风格 messages 合并为一条 canonical user message。

        canonical 形态：
            {"role": "user", "blocks": [tool_result..., image..., text...]}
        tool_result blocks 排在前面，便于 provider 把它们与上一轮的 tool_use 配对。
        """
        text_blocks: list[dict] = []
        image_blocks: list[dict] = []
        tool_result_blocks: list[dict] = []
        answered_ids: set[str] = set()

        for msg in messages:
            if msg.get("role") == "system":
                continue
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                text_blocks.append({"type": "text", "text": content})

            for img in msg.get("images", []):
                image_blocks.append({
                    "type": "image",
                    "media_type": img.get("media_type", "image/jpeg"),
                    "data": img.get("data", ""),
                })

            for tr in msg.get("tool_results", []):
                tool_use_id = tr.get("tool_use_id", "")
                result_content = tr.get("content", "")
                if not tool_use_id:
                    text_blocks.insert(0, {
                        "type": "text",
                        "text": f"<tool_result>{result_content}</tool_result>",
                    })
                    continue
                answered_ids.add(tool_use_id)
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result_content,
                    "is_error": bool(tr.get("is_error", False)),
                })

        for tid in self._pending_tool_ids:
            if tid not in answered_ids:
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "content": "",
                    "is_error": False,
                })
        self._pending_tool_ids = []

        return {"role": "user", "blocks": tool_result_blocks + image_blocks + text_blocks}

    @property
    def last_usage(self) -> Usage:
        return self.tracker.last_usage

    @last_usage.setter
    def last_usage(self, value: Usage) -> None:
        self.tracker.last_usage = value

    @property
    def task_usage(self) -> Usage:
        return self.tracker.task_usage

    def reset_task_usage(self) -> None:
        self.tracker.begin_task()
