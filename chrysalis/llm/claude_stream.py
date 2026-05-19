"""Anthropic Messages 协议的流式请求与 SSE 解析。

支持 Claude 原生 API 及兼容的中转站。
"""

import json
from typing import Generator

import requests

from chrysalis.llm.openai_stream import stream_with_retry
from chrysalis.llm.types import Response, SessionConfig, ToolCall, Usage


def claude_stream(
    config: SessionConfig,
    messages: list[dict],
    system: str,
    tools: list[dict] | None,
) -> Generator[str, None, Response]:
    """发起 Anthropic messages 流式请求，yield 文本块，return Response。

    messages 必须是已转换为 Anthropic wire format 的数组（见 protocols.to_anthropic_messages）。
    """
    url = f"{config.base_url}/messages"
    headers = {
        "x-api-key": config.api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = _build_payload(config, messages, system, tools)
    return (yield from stream_with_retry(config, url, headers, payload, _parse_claude_sse))


def _build_payload(
    config: SessionConfig,
    messages: list[dict],
    system: str,
    tools: list[dict] | None,
) -> dict:
    payload: dict = {
        "model": config.model,
        "messages": messages,
        "stream": True,
        "max_tokens": config.max_tokens or 4096,
    }
    if system:
        payload["system"] = system
    if config.temperature is not None:
        payload["temperature"] = config.temperature
    if tools:
        payload["tools"] = _to_claude_tools(tools)
    if config.thinking != "disabled":
        payload["thinking"] = {
            "type": config.thinking,
            "budget_tokens": config.thinking_budget or 10000,
        }
        payload.pop("temperature", None)
    return payload


def _to_claude_tools(tools: list[dict]) -> list[dict]:
    """将 OpenAI 格式的 tools 转为 Claude 格式。"""
    result = []
    for t in tools:
        if "input_schema" in t:
            result.append(t)
            continue
        fn = t.get("function", t)
        result.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return result


def _parse_claude_sse(http_response: requests.Response) -> Generator[str, None, Response]:
    """解析 Anthropic SSE 事件流。

    事件格式：
        event: content_block_start
        data: {"type": "content_block_start", "content_block": {...}}

    支持的 block 类型：text, thinking, tool_use
    """
    content_parts: list[str] = []
    thinking_parts: list[str] = []
    thinking_signature = ""
    tool_calls: list[ToolCall] = []
    current_block_type: str = ""
    current_tool: dict | None = None
    stop_reason = "end_turn"
    usage = Usage()

    event_type = ""
    for raw_line in http_response.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8", errors="replace")
        if line.startswith("event: "):
            event_type = line[7:].strip()
            continue
        if not line.startswith("data: "):
            continue
        raw_data = line[6:]
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            continue

        if event_type == "message_start":
            msg = data.get("message", {})
            u = msg.get("usage", {})
            usage.prompt_tokens = u.get("input_tokens", 0)
            usage.cache_read_tokens = u.get("cache_read_input_tokens", 0)
            usage.cache_creation_tokens = u.get("cache_creation_input_tokens", 0)

        elif event_type == "content_block_start":
            block = data.get("content_block", {})
            current_block_type = block.get("type", "")
            if current_block_type == "tool_use":
                current_tool = {
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "arguments": "",
                }

        elif event_type == "content_block_delta":
            delta = data.get("delta", {})
            delta_type = delta.get("type", "")
            if delta_type == "text_delta":
                text = delta.get("text", "")
                content_parts.append(text)
                yield text
            elif delta_type == "thinking_delta":
                thinking_parts.append(delta.get("thinking", ""))
            elif delta_type == "signature_delta":
                thinking_signature += delta.get("signature", "")
            elif delta_type == "input_json_delta":
                if current_tool is not None:
                    current_tool["arguments"] += delta.get("partial_json", "")

        elif event_type == "content_block_stop":
            if current_block_type == "tool_use" and current_tool:
                tool_calls.append(ToolCall(
                    id=current_tool["id"],
                    name=current_tool["name"],
                    arguments=current_tool["arguments"],
                ))
                current_tool = None
            current_block_type = ""

        elif event_type == "message_delta":
            delta = data.get("delta", {})
            if delta.get("stop_reason"):
                stop_reason = delta["stop_reason"]
            u = data.get("usage", {})
            if u:
                usage.completion_tokens = u.get("output_tokens", 0)

        elif event_type == "message_stop":
            break

        elif event_type == "error":
            error_msg = data.get("error", {}).get("message", str(data))
            error_text = f"!!!Error: {error_msg}"
            yield error_text
            return Response(content=error_text, raw=error_text)

    if tool_calls:
        stop_reason = "tool_use"

    usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
    content = "".join(content_parts)
    return Response(
        content=content,
        thinking="".join(thinking_parts),
        thinking_signature=thinking_signature,
        tool_calls=tool_calls,
        raw=content,
        stop_reason=stop_reason,
        usage=usage,
    )
