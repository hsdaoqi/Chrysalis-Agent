"""Canonical history → 协议特定 wire format 的转换器。

canonical 格式见 chrysalis/llm/types.py 顶部说明。
"""

import json


# ── Anthropic ──

def to_anthropic_messages(history: list[dict]) -> list[dict]:
    """canonical history → Anthropic /messages 的 messages 数组。

    Anthropic 协议下：
    - text / tool_use 在 assistant message 的 content blocks 里
    - tool_result 在 user message 的 content blocks 里
    - thinking block 仅当带 signature 时输出（无 signature 发上去会被官方拒）
    """
    out: list[dict] = []
    for msg in history:
        role = msg.get("role", "")
        blocks = msg.get("blocks", [])
        if role == "system":
            continue
        content_blocks = []
        for block in blocks:
            btype = block.get("type", "")
            if btype == "text":
                text = block.get("text", "")
                if text:
                    content_blocks.append({"type": "text", "text": text})
            elif btype == "thinking":
                signature = block.get("signature", "")
                text = block.get("text", "")
                if signature and text:
                    content_blocks.append({
                        "type": "thinking",
                        "thinking": text,
                        "signature": signature,
                    })
            elif btype == "tool_use":
                content_blocks.append({
                    "type": "tool_use",
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "input": _parse_arguments(block.get("arguments", "")),
                })
            elif btype == "image":
                content_blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": block.get("media_type", "image/jpeg"),
                        "data": block.get("data", ""),
                    },
                })
            elif btype == "tool_result":
                entry: dict = {
                    "type": "tool_result",
                    "tool_use_id": block.get("tool_use_id", ""),
                    "content": block.get("content", ""),
                }
                if block.get("is_error"):
                    entry["is_error"] = True
                content_blocks.append(entry)
        if not content_blocks:
            continue
        out.append({"role": role, "content": content_blocks})
    return out


# ── OpenAI ──

def to_openai_messages(history: list[dict], system: str = "") -> list[dict]:
    """canonical history → OpenAI /chat/completions 的 messages 数组。

    OpenAI 协议下：
    - thinking blocks 直接丢弃（协议不支持）
    - assistant 的 tool_use → 同条 assistant message 的 tool_calls 字段
    - tool_result → 单独的 {role: "tool", tool_call_id, content} 消息
    """
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})

    for msg in history:
        role = msg.get("role", "")
        blocks = msg.get("blocks", [])
        if role == "system":
            text = _collect_text(blocks)
            if text:
                out.append({"role": "system", "content": text})
            continue

        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for block in blocks:
                btype = block.get("type", "")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": block.get("arguments", "") or "{}",
                        },
                    })
            entry: dict = {"role": "assistant", "content": "".join(text_parts)}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
            continue

        # user 角色：tool_result 拆成独立 role:tool 消息，其余文本/图片合并
        text_parts: list[str] = []
        image_parts: list[dict] = []
        for block in blocks:
            btype = block.get("type", "")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "image":
                image_parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{block.get('media_type', 'image/jpeg')};base64,{block.get('data', '')}",
                    },
                })
            elif btype == "tool_result":
                # 先 flush 累积的 user 文本（保持顺序）
                if text_parts:
                    text = "".join(text_parts).strip()
                    if text:
                        out.append({"role": "user", "content": text})
                    text_parts = []
                out.append({
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": block.get("content", ""),
                })
        if text_parts or image_parts:
            text = "".join(text_parts).strip()
            if image_parts:
                content: list[dict] = []
                if text:
                    content.append({"type": "text", "text": text})
                content.extend(image_parts)
                out.append({"role": "user", "content": content})
            elif text:
                out.append({"role": "user", "content": text})

    return _sanitize_tool_pairs(out)


# ── helpers ──

def _sanitize_tool_pairs(messages: list[dict]) -> list[dict]:
    """只保留仍然成对的 OpenAI tool_call/tool 消息。

    OpenAI 要求 assistant tool_calls 后面紧跟对应 role:tool 消息。
    如果 compact 或加载旧会话打断了配对，移除断裂的协议块，不伪造结果。
    """
    result: list[dict] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "tool":
            i += 1
            continue

        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            expected = {
                tc.get("id"): tc
                for tc in msg.get("tool_calls", [])
                if tc.get("id")
            }
            j = i + 1
            tool_messages: list[dict] = []
            answered_ids: set[str] = set()
            while j < len(messages) and messages[j].get("role") == "tool":
                tid = messages[j].get("tool_call_id", "")
                if tid in expected:
                    answered_ids.add(tid)
                    tool_messages.append(messages[j])
                j += 1

            kept_calls = [
                tc for tc in msg.get("tool_calls", [])
                if tc.get("id") in answered_ids
            ]
            entry = dict(msg)
            if kept_calls:
                entry["tool_calls"] = kept_calls
                result.append(entry)
                result.extend(tool_messages)
            else:
                entry.pop("tool_calls", None)
                if entry.get("content"):
                    result.append(entry)
            i = j
        else:
            result.append(msg)
            i += 1
    return result


def _parse_arguments(arguments: str) -> dict:
    """canonical 里 arguments 是字符串，Anthropic 协议要 dict 形态的 input。"""
    if not arguments:
        return {}
    try:
        parsed = json.loads(arguments)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _collect_text(blocks: list[dict]) -> str:
    parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    return "".join(parts)
