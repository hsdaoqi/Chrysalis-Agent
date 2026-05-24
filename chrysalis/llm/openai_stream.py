"""OpenAI Chat Completions 协议的流式请求与 SSE 解析。

支持所有 OpenAI 兼容的 API（OpenAI、DeepSeek、各种中转站）。
"""

import json
import threading
import time
from typing import Generator

import requests

from chrysalis.llm.types import Response, SessionConfig, ToolCall, Usage

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504, 529}


def openai_stream(
    config: SessionConfig,
    messages: list[dict],
    system: str,
    tools: list[dict] | None,
    cancel_event: threading.Event | None = None,
) -> Generator[str, None, Response]:
    """发起 OpenAI chat/completions 流式请求，yield 文本块，return Response。

    messages 必须是已转换为 OpenAI wire format 的数组（见 protocols.to_openai_messages），
    system prompt 已经在转换时被合并为首条 system message，本函数不再追加。
    """
    url = f"{config.base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    payload = _build_payload(config, messages, tools)
    return (yield from stream_with_retry(config, url, headers, payload, _parse_openai_sse, cancel_event=cancel_event))


def _build_payload(
    config: SessionConfig,
    messages: list[dict],
    tools: list[dict] | None,
) -> dict:
    payload: dict = {
        "model": config.model,
        "messages": messages,
        "stream": config.stream,
        "temperature": config.temperature,
    }
    if config.stream:
        payload["stream_options"] = {"include_usage": True}
    if config.max_tokens:
        payload["max_tokens"] = config.max_tokens
    if tools:
        payload["tools"] = tools
    return payload


def _parse_openai_sse(
    http_response: requests.Response,
    cancel_event: threading.Event | None = None,
) -> Generator[str, None, Response]:
    """解析 OpenAI SSE 流，提取 content delta 和 tool_calls。"""
    content_parts: list[str] = []
    tool_calls_map: dict[int, dict] = {}
    reasoning_parts: list[str] = []
    usage = Usage()

    for raw_line in http_response.iter_lines():
        if cancel_event is not None and cancel_event.is_set():
            http_response.close()
            return Response(content="", raw="", stop_reason="cancelled", cancelled=True)
        if not raw_line:
            continue
        line = raw_line.decode("utf-8", errors="replace")
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue

        if "usage" in chunk and chunk["usage"]:
            u = chunk["usage"]
            usage = Usage(
                prompt_tokens=u.get("prompt_tokens", 0),
                completion_tokens=u.get("completion_tokens", 0),
                total_tokens=u.get("total_tokens", 0),
                cache_read_tokens=u.get("prompt_tokens_details", {}).get("cached_tokens", 0),
            )

        choices = chunk.get("choices")
        if not choices:
            continue
        delta = choices[0].get("delta", {})

        if delta.get("content"):
            content_parts.append(delta["content"])
            yield delta["content"]

        if delta.get("reasoning_content"):
            reasoning_parts.append(delta["reasoning_content"])

        if delta.get("tool_calls"):
            for tc in delta["tool_calls"]:
                idx = tc.get("index", 0)
                if idx not in tool_calls_map:
                    tool_calls_map[idx] = {"id": "", "name": "", "arguments": ""}
                entry = tool_calls_map[idx]
                if tc.get("id"):
                    entry["id"] = tc["id"]
                fn = tc.get("function", {})
                if fn.get("name"):
                    entry["name"] = fn["name"]
                if fn.get("arguments"):
                    entry["arguments"] += fn["arguments"]

    tool_calls = [
        ToolCall(id=v["id"], name=v["name"], arguments=v["arguments"])
        for _, v in sorted(tool_calls_map.items())
        if v["name"]
    ]
    content = "".join(content_parts)
    thinking = "".join(reasoning_parts)
    stop = "tool_use" if tool_calls else "end_turn"
    return Response(content=content, thinking=thinking, tool_calls=tool_calls, raw=content, stop_reason=stop, usage=usage)


# ── 通用流式重试 ──

def stream_with_retry(
    config: SessionConfig,
    url: str,
    headers: dict,
    payload: dict,
    parse_fn,
    cancel_event: threading.Event | None = None,
) -> Generator[str, None, Response]:
    """带指数退避重试的流式 HTTP 请求。"""
    proxies = {"http": config.proxy, "https": config.proxy} if config.proxy else None

    for attempt in range(config.max_retries + 1):
        if cancel_event is not None and cancel_event.is_set():
            return Response(content="", raw="", stop_reason="cancelled", cancelled=True)
        try:
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=(config.connect_timeout, config.read_timeout),
                proxies=proxies,
            )
            if cancel_event is not None:
                def _watch_cancel() -> None:
                    cancel_event.wait()
                    try:
                        resp.close()
                    except Exception:
                        pass

                threading.Thread(target=_watch_cancel, daemon=True).start()
            if resp.status_code >= 400:
                body = ""
                try:
                    body = resp.text[:300]
                except Exception:
                    pass
                if resp.status_code in RETRYABLE_STATUS and attempt < config.max_retries:
                    time.sleep(_backoff_delay(attempt, resp))
                    continue
                error_text = f"!!!Error: HTTP {resp.status_code} {body}"
                yield error_text
                return Response(content=error_text, raw=error_text)

            resp.encoding = "utf-8"
            return (yield from parse_fn(resp, cancel_event=cancel_event))

        except (requests.Timeout, requests.ConnectionError) as exc:
            if cancel_event is not None and cancel_event.is_set():
                return Response(content="", raw="", stop_reason="cancelled", cancelled=True)
            if attempt < config.max_retries:
                time.sleep(_backoff_delay(attempt))
                continue
            error_text = f"!!!Error: {type(exc).__name__} (attempt {attempt + 1}/{config.max_retries + 1})"
            yield error_text
            return Response(content=error_text, raw=error_text)

        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                return Response(content="", raw="", stop_reason="cancelled", cancelled=True)
            error_text = f"!!!Error: {type(exc).__name__}: {exc}"
            yield error_text
            return Response(content=error_text, raw=error_text)

    error_text = "!!!Error: 超过最大重试次数"
    yield error_text
    return Response(content=error_text, raw=error_text)


def _backoff_delay(attempt: int, resp: requests.Response | None = None) -> float:
    """指数退避，尊重 Retry-After header。"""
    if resp is not None:
        try:
            ra = float(resp.headers.get("retry-after", ""))
            return max(0.5, ra)
        except (ValueError, TypeError):
            pass
    return min(30.0, 1.5 * (2 ** attempt))
