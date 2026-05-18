"""统一 LLM Client：agent_loop 直接调用的接口。

负责：
- 将 agent_loop 传来的 messages（含 tool_results）合并为 session 能理解的格式
- 透传流式输出
- 记录原始 prompt/response 日志
- 跟踪 pending tool_use IDs
"""

import json
from typing import Generator

from chrysalis.llm.logger import write_llm_log
from chrysalis.llm.session import BaseSession
from chrysalis.llm.types import Response, SessionConfig


class LLMClient:
    """agent_loop 的唯一 LLM 接口。"""

    def __init__(self, session: BaseSession):
        self.session = session
        self._pending_tool_ids: list[str] = []

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

        merged = self._merge_user_message(messages)
        write_llm_log("Prompt", json.dumps(merged, ensure_ascii=False, default=str))

        gen = self.session.ask(merged)
        response: Response | None = None
        try:
            while True:
                chunk = next(gen)
                yield chunk
        except StopIteration as e:
            response = e.value

        if response is None:
            response = Response(content="!!!Error: 未收到响应", raw="")

        write_llm_log("Response", response.raw or response.content)

        if response.tool_calls:
            self._pending_tool_ids = [tc.id for tc in response.tool_calls]
        else:
            self._pending_tool_ids = []

        return response

    def _merge_user_message(self, messages: list[dict]) -> dict:
        """将 messages 列表合并为一条 user message（含 tool_result blocks）。"""
        content_blocks: list = []
        tool_result_blocks: list = []
        answered_ids: set[str] = set()

        for msg in messages:
            if msg.get("role") == "system":
                continue
            raw_content = msg.get("content", "")
            if isinstance(raw_content, str) and raw_content:
                content_blocks.append({"type": "text", "text": raw_content})
            elif isinstance(raw_content, list):
                content_blocks.extend(raw_content)

            for tr in msg.get("tool_results", []):
                tool_use_id = tr.get("tool_use_id", "")
                result_content = tr.get("content", "")
                answered_ids.add(tool_use_id)
                if tool_use_id:
                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result_content,
                    })
                else:
                    content_blocks.insert(0, {
                        "type": "text",
                        "text": f"<tool_result>{result_content}</tool_result>",
                    })

        for tid in self._pending_tool_ids:
            if tid not in answered_ids:
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "content": "",
                })
        self._pending_tool_ids = []

        all_content = tool_result_blocks + content_blocks
        if len(all_content) == 1 and all_content[0].get("type") == "text":
            return {"role": "user", "content": all_content[0]["text"]}
        return {"role": "user", "content": all_content}
