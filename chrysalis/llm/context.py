"""上下文窗口管理：canonical history 的裁剪与压缩。

canonical 格式见 chrysalis/llm/types.py 顶部说明。每条消息形如：
    {"role": ..., "blocks": [...], "_compressed": bool}
裁剪与压缩只针对 blocks 列表，不再处理任何协议特定字段。

缓存友好：消息只压缩一次，压缩后打标记不再修改。
"""

import json
import re

_COMPRESSIBLE_TAGS = (
    "thinking", "tool_use", "tool_result", "history",
    "key_info", "earlier_context",
)
_TAG_PATTERN = re.compile(
    r"<(" + "|".join(_COMPRESSIBLE_TAGS) + r")>(.*?)</\1>",
    re.DOTALL,
)
_COMPRESSED_KEY = "_compressed"
_MAX_TOOL_CONTENT = 800
_MAX_TOOL_ARGS = 400


def trim_messages_history(history: list[dict], context_window: int) -> None:
    """原地裁剪 history，保证总字符数不超过 context_window * 3。"""
    compress_history_tags(history)
    budget = context_window * 3
    cost = _calc_cost(history)
    if cost <= budget:
        return
    compress_history_tags(history, keep_recent=4, force=True)
    target = int(budget * 0.6)
    while len(history) > 5 and _calc_cost(history) > target:
        _drop_oldest_turn(history)


def compress_history_tags(
    history: list[dict],
    keep_recent: int = 10,
    max_len: int = 800,
    force: bool = False,
) -> None:
    """截断旧消息中的大块内容。每条消息只压缩一次（force=True 时强制再压一遍）。"""
    if len(history) <= keep_recent:
        return
    for msg in history[:-keep_recent]:
        if msg.get(_COMPRESSED_KEY) and not force:
            continue
        _compress_message(msg, max_len)
        msg[_COMPRESSED_KEY] = True


def _compress_message(msg: dict, max_len: int) -> None:
    """压缩单条 canonical 消息的 blocks。"""
    blocks = msg.get("blocks")
    if not isinstance(blocks, list):
        return
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype == "text":
            text = block.get("text", "")
            if len(text) >= max_len:
                block["text"] = _TAG_PATTERN.sub(
                    lambda m: _truncate_tag(m, max_len), text
                )
        elif btype == "thinking":
            text = block.get("text", "")
            if len(text) > max_len:
                block["text"] = _truncate_text(text, max_len)
        elif btype == "tool_use":
            args = block.get("arguments", "")
            if isinstance(args, str) and len(args) > _MAX_TOOL_ARGS:
                block["arguments"] = _truncate_text(args, _MAX_TOOL_ARGS)
        elif btype == "tool_result":
            content = block.get("content", "")
            if isinstance(content, str) and len(content) > _MAX_TOOL_CONTENT:
                block["content"] = _truncate_text(content, _MAX_TOOL_CONTENT)


def _drop_oldest_turn(history: list[dict]) -> None:
    """删除最旧的一个完整 turn。

    保证：
    - 删除后首条仍是 user role
    - 不残留孤立的 tool_result（其对应的 tool_use 已被删）
    """
    if not history:
        return
    history.pop(0)
    while history and history[0].get("role") == "assistant":
        history.pop(0)
    if history and history[0].get("role") == "user":
        _sanitize_leading_message(history[0])


def _sanitize_leading_message(msg: dict) -> None:
    """首条 user message 不能含孤立的 tool_result（缺对应 tool_use）。"""
    blocks = msg.get("blocks")
    if not isinstance(blocks, list):
        return
    cleaned: list[dict] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            text = block.get("content", "")
            if text:
                cleaned.append({
                    "type": "text",
                    "text": f"[之前的工具结果] {text[:200]}",
                })
        else:
            cleaned.append(block)
    msg["blocks"] = cleaned


def _truncate_tag(match: re.Match, max_len: int) -> str:
    tag, body = match.group(1), match.group(2)
    if len(body) <= max_len:
        return match.group(0)
    half = max_len // 2
    truncated = body[:half] + "...[Truncated]..." + body[-half:]
    return f"<{tag}>{truncated}</{tag}>"


def _truncate_text(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    half = max_len // 2
    return text[:half] + "...[Truncated]..." + text[-half:]


def _calc_cost(history: list[dict]) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in history)
