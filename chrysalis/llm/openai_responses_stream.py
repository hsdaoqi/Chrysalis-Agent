"""OpenAI Responses API streaming client."""

import json
import threading
import time
import uuid
from typing import Generator

import requests

from chrysalis.llm.openai_stream import RETRYABLE_STATUS, _backoff_delay
from chrysalis.llm.protocols import to_openai_responses_tools
from chrysalis.llm.types import Response, SessionConfig, ToolCall, Usage


def openai_responses_stream(
    config: SessionConfig,
    input_items: list[dict],
    system: str,
    tools: list[dict] | None,
    cancel_event: threading.Event | None = None,
) -> Generator[str, None, Response]:
    url = f"{config.base_url}/responses"
    request_id = str(uuid.uuid4())
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "Codex Desktop/0.118.0 (Windows 10.0.26200; x86_64) unknown (chrysalis; 0.1.0)",
        "Originator": "Codex Desktop",
        "session_id": request_id,
        "x-client-request-id": request_id,
        "x-codex-turn-metadata": json.dumps({
            "session_id": request_id,
            "turn_id": str(uuid.uuid4()),
            "workspaces": {},
            "sandbox": "none",
        }),
    }
    payload = _build_payload(config, input_items, system, tools)
    return (yield from _stream_with_retry(config, url, headers, payload, cancel_event=cancel_event))


def _build_payload(
    config: SessionConfig,
    input_items: list[dict],
    system: str,
    tools: list[dict] | None,
) -> dict:
    payload: dict = {
        "model": config.model,
        "instructions": system or "",
        "input": input_items,
        "tools": to_openai_responses_tools(tools),
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "store": False,
        "stream": config.stream,
        "include": ["reasoning.encrypted_content"],
        "prompt_cache_key": f"chrysalis-{config.name or config.model}",
    }
    effort = _reasoning_effort(config)
    if effort:
        payload["reasoning"] = {"effort": effort, "summary": "auto"}
    if config.max_tokens:
        payload["max_output_tokens"] = config.max_tokens
    return payload


def _reasoning_effort(config: SessionConfig) -> str:
    value = (config.thinking or "").strip().lower()
    if value in {"minimal", "low", "medium", "high", "xhigh"}:
        return value
    return "high" if config.wire_api == "responses" else ""


def _stream_with_retry(
    config: SessionConfig,
    url: str,
    headers: dict,
    payload: dict,
    cancel_event: threading.Event | None = None,
) -> Generator[str, None, Response]:
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
                    body = _decode_body(resp.text)[:300]
                except Exception:
                    pass
                if resp.status_code in RETRYABLE_STATUS and attempt < config.max_retries:
                    time.sleep(_backoff_delay(attempt, resp))
                    continue
                error_text = f"!!!Error: HTTP {resp.status_code} {body}"
                yield error_text
                return Response(content=error_text, raw=error_text)

            resp.encoding = "utf-8"
            return (yield from _parse_responses_sse(resp, cancel_event=cancel_event))

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

    error_text = "!!!Error: exceeded maximum retries"
    yield error_text
    return Response(content=error_text, raw=error_text)


def _parse_responses_sse(
    http_response: requests.Response,
    cancel_event: threading.Event | None = None,
) -> Generator[str, None, Response]:
    content_parts: list[str] = []
    thinking_parts: list[str] = []
    function_items: dict[str, dict] = {}
    usage = Usage()
    raw_events: list[str] = []

    for raw_line in http_response.iter_lines(decode_unicode=True):
        if cancel_event is not None and cancel_event.is_set():
            http_response.close()
            return Response(content="", raw="", stop_reason="cancelled", cancelled=True)
        if not raw_line or not raw_line.startswith("data: "):
            continue
        data = raw_line[6:]
        if data.strip() == "[DONE]":
            break
        raw_events.append(data)
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue

        if isinstance(event.get("error"), dict):
            error_text = f"!!!Error: {event['error'].get('message') or data}"
            yield error_text
            return Response(content=error_text, raw=data)

        event_type = event.get("type", "")
        if event_type == "response.output_text.delta":
            delta = event.get("delta", "")
            if delta:
                content_parts.append(delta)
                yield delta
        elif event_type == "response.reasoning_summary_text.delta":
            delta = event.get("delta", "")
            if delta:
                thinking_parts.append(delta)
        elif event_type == "response.function_call_arguments.delta":
            item_id = event.get("item_id") or event.get("call_id") or ""
            if item_id:
                entry = function_items.setdefault(item_id, {"id": item_id, "name": "", "arguments": ""})
                entry["arguments"] += event.get("delta", "")
        elif event_type in {"response.output_item.added", "response.output_item.done"}:
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                item_id = item.get("id") or item.get("call_id") or ""
                call_id = item.get("call_id") or item_id
                entry = function_items.setdefault(item_id, {"id": call_id, "name": "", "arguments": ""})
                entry["id"] = call_id
                entry["name"] = item.get("name") or entry["name"]
                entry["arguments"] = item.get("arguments") or entry["arguments"]
        elif event_type == "response.completed":
            response = event.get("response") or {}
            usage = _parse_usage(response.get("usage") or {})
            _collect_completed_output(response, content_parts, function_items)

    tool_calls = [
        ToolCall(id=value["id"], name=value["name"], arguments=value["arguments"] or "{}")
        for value in function_items.values()
        if value.get("name")
    ]
    content = "".join(content_parts)
    thinking = "".join(thinking_parts)
    stop = "tool_use" if tool_calls else "end_turn"
    return Response(
        content=content,
        thinking=thinking,
        tool_calls=tool_calls,
        raw="\n".join(raw_events) or content,
        stop_reason=stop,
        usage=usage,
    )


def _collect_completed_output(response: dict, content_parts: list[str], function_items: dict[str, dict]) -> None:
    if content_parts:
        return
    for item in response.get("output") or []:
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if part.get("type") in {"output_text", "text"} and part.get("text"):
                    content_parts.append(part["text"])
        elif item.get("type") == "function_call":
            item_id = item.get("id") or item.get("call_id") or ""
            call_id = item.get("call_id") or item_id
            entry = function_items.setdefault(item_id, {"id": call_id, "name": "", "arguments": ""})
            entry["id"] = call_id
            entry["name"] = item.get("name") or entry["name"]
            entry["arguments"] = item.get("arguments") or entry["arguments"]


def _parse_usage(raw: dict) -> Usage:
    prompt = int(raw.get("input_tokens") or raw.get("prompt_tokens") or 0)
    completion = int(raw.get("output_tokens") or raw.get("completion_tokens") or 0)
    total = int(raw.get("total_tokens") or prompt + completion)
    details = raw.get("input_tokens_details") or raw.get("prompt_tokens_details") or {}
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cache_read_tokens=int(details.get("cached_tokens") or 0),
    )


def _decode_body(text: str) -> str:
    try:
        return text.encode("latin1").decode("utf-8")
    except Exception:
        return text
