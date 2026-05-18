"""上下文窗口管理：history 裁剪与压缩。

支持 function calling 模式下的 tool_calls / role:"tool" 消息格式。
缓存友好设计：消息只压缩一次，压缩后打标记不再修改，保持前缀稳定以提高 token 缓存命中率。
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
    compress_history_tags(history, keep_recent=4)
    target = int(budget * 0.6)
    while len(history) > 5 and _calc_cost(history) > target:
        _drop_oldest_turn(history)


def compress_history_tags(history: list[dict], keep_recent: int = 10, max_len: int = 800) -> None:
    """截断旧消息中的大块内容。每条消息只压缩一次。"""
    if len(history) <= keep_recent:
        return
    for msg in history[:-keep_recent]:
        if msg.get(_COMPRESSED_KEY):
            continue
        _compress_message(msg, max_len)
        msg[_COMPRESSED_KEY] = True


def _compress_message(msg: dict, max_len: int) -> None:
    """压缩单条消息，处理所有格式。"""
    role = msg.get("role", "")

    # role: "tool" — 压缩工具结果内容
    if role == "tool":
        content = msg.get("content", "")
        if isinstance(content, str) and len(content) > _MAX_TOOL_CONTENT:
            msg["content"] = _truncate_text(content, _MAX_TOOL_CONTENT)
        return

    # assistant with tool_calls — 压缩 arguments
    if role == "assistant" and msg.get("tool_calls"):
        for tc in msg["tool_calls"]:
            fn = tc.get("function", {})
            args = fn.get("arguments", "")
            if isinstance(args, str) and len(args) > _MAX_TOOL_ARGS:
                fn["arguments"] = _truncate_text(args, _MAX_TOOL_ARGS)
        return

    # 普通文本消息 — 压缩标签内容
    content = _get_text_content(msg)
    if not content or len(content) < max_len:
        return
    new_content = _TAG_PATTERN.sub(
        lambda m: _truncate_tag(m, max_len), content
    )
    if new_content != content:
        _set_text_content(msg, new_content)

    # content 是 list 时，压缩 tool_use/tool_result blocks
    raw_content = msg.get("content")
    if isinstance(raw_content, list):
        for block in raw_content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "tool_use":
                inp = block.get("input", "")
                if isinstance(inp, str) and len(inp) > _MAX_TOOL_ARGS:
                    block["input"] = _truncate_text(inp, _MAX_TOOL_ARGS)
            elif btype == "tool_result":
                rc = block.get("content", "")
                if isinstance(rc, str) and len(rc) > _MAX_TOOL_CONTENT:
                    block["content"] = _truncate_text(rc, _MAX_TOOL_CONTENT)


def _drop_oldest_turn(history: list[dict]) -> None:
    """删除最旧的一个完整 turn（保持 assistant+tool 消息对完整性）。"""
    if not history:
        return
    history.pop(0)
    # 如果删掉 user 后下一条是 assistant+tool_calls，继续删到下一个 user
    while history and history[0].get("role") in ("assistant", "tool"):
        history.pop(0)
    # 确保首条是 user
    if history and history[0].get("role") == "user":
        _sanitize_leading_message(history[0])


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


def _get_text_content(msg: dict) -> str:
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(parts)
    return ""


def _set_text_content(msg: dict, new_text: str) -> None:
    content = msg.get("content", "")
    if isinstance(content, str):
        msg["content"] = new_text
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                block["text"] = new_text
                return


def _sanitize_leading_message(msg: dict) -> None:
    """确保 history 首条 user message 不含孤立的 tool_result blocks。"""
    content = msg.get("content")
    if not isinstance(content, list):
        return
    cleaned = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            text = block.get("content", "")
            if text:
                cleaned.append({"type": "text", "text": f"[之前的工具结果] {text[:200]}"})
        else:
            cleaned.append(block)
    msg["content"] = cleaned
