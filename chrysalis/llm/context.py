"""Runtime context compaction for canonical LLM history.

This is the Claude Code-style companion to the OpenClaw-style Context Engine:

1. microcompact old bulky blocks, preserving recent turns verbatim;
2. full compact early turns into a structured summary near the soft limit;
3. hard trim whole turns only as a last resort.

All stages keep tool_use/tool_result pairs protocol-valid.
"""

from __future__ import annotations

import json
import re

_COMPRESSIBLE_TAGS = (
    "thinking",
    "tool_use",
    "tool_result",
    "history",
    "key_info",
    "earlier_context",
    "earlier_summary",
    "recent_turns",
)
_TAG_PATTERN = re.compile(
    r"<(" + "|".join(_COMPRESSIBLE_TAGS) + r")>(.*?)</\1>",
    re.DOTALL,
)

_MICROCOMPACT_KEY = "_microcompact"
_FULL_COMPACT_KEY = "_full_compact"
_MAX_TEXT_TAG = 800
_MAX_THINKING = 500
_MAX_TOOL_CONTENT = 900
_MAX_TOOL_ARGS = 500
_RECENT_TURNS_TO_KEEP = 8
_SOFT_LIMIT_RATIO = 0.72


def trim_messages_history(history: list[dict], context_window: int) -> None:
    """Compact history in place without breaking provider tool protocols."""

    budget = max(1, context_window * 3)
    soft_budget = max(1, int(budget * _SOFT_LIMIT_RATIO))

    microcompact_history(history)
    repair_tool_pairs(history)

    if _calc_cost(history) > soft_budget:
        full_compact_history(history, target_chars=soft_budget)
        repair_tool_pairs(history)

    while len(history) > 4 and _calc_cost(history) > budget:
        drop_oldest_turn(history)
        repair_tool_pairs(history)


def compress_history_tags(
    history: list[dict],
    keep_recent: int = 10,
    max_len: int = 800,
    force: bool = False,
) -> None:
    """Backward-compatible entry point for tests and older callers."""

    microcompact_history(
        history,
        keep_recent=keep_recent,
        max_text_tag=max_len,
        force=force,
    )


def microcompact_history(
    history: list[dict],
    keep_recent: int = 10,
    max_text_tag: int = _MAX_TEXT_TAG,
    force: bool = False,
) -> None:
    """Shrink bulky old blocks while preserving message structure."""

    if len(history) <= keep_recent and not force:
        return
    candidates = history if force else history[:-keep_recent]
    for msg in candidates:
        if msg.get(_MICROCOMPACT_KEY) and not force:
            continue
        _microcompact_message(msg, max_text_tag)
        msg[_MICROCOMPACT_KEY] = True


def full_compact_history(
    history: list[dict],
    target_chars: int,
    keep_recent_turns: int = _RECENT_TURNS_TO_KEEP,
) -> None:
    """Fold early history into one structured user summary message.

    This is deterministic for now.  An LLM summarizer can later replace
    _summarize_messages while keeping the same insertion point and invariant:
    the summary must preserve identifiers such as paths, commands, tool names,
    errors, decisions, and unfinished tasks.
    """

    if any(msg.get(_FULL_COMPACT_KEY) for msg in history):
        return

    split = _split_for_full_compact(history, keep_recent_turns)
    if split <= 0:
        return

    early = history[:split]
    recent = history[split:]
    summary = _summarize_messages(early)
    if not summary:
        return

    summary_msg = {
        "role": "user",
        "blocks": [{"type": "text", "text": summary}],
        _MICROCOMPACT_KEY: True,
        _FULL_COMPACT_KEY: True,
    }
    history[:] = [summary_msg] + recent

    if _calc_cost(history) > target_chars and len(history) > 4:
        microcompact_history(history, keep_recent=keep_recent_turns, force=True)


def drop_oldest_turn(history: list[dict]) -> None:
    """Drop the oldest complete user/assistant/tool exchange."""

    if not history:
        return
    end = _first_turn_end(history)
    del history[:end]


def repair_tool_pairs(history: list[dict]) -> None:
    """Remove broken protocol blocks caused by loading or trimming history."""

    for i, msg in enumerate(history):
        role = msg.get("role")
        blocks = msg.get("blocks")
        if not isinstance(blocks, list):
            continue

        if role == "assistant":
            tool_ids = _tool_use_ids(msg)
            if not tool_ids:
                continue
            next_msg = history[i + 1] if i + 1 < len(history) else None
            next_ids = _tool_result_ids(next_msg) if next_msg else set()
            _strip_tool_uses(msg, tool_ids - next_ids)

        elif role == "user":
            prev_msg = history[i - 1] if i > 0 else None
            valid_ids = _tool_use_ids(prev_msg) if prev_msg else set()
            _convert_orphan_tool_results(msg, valid_ids)

    history[:] = [msg for msg in history if _has_blocks(msg)]


def _microcompact_message(msg: dict, max_text_tag: int) -> None:
    blocks = msg.get("blocks")
    if not isinstance(blocks, list):
        return
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype == "text":
            text = block.get("text", "")
            if len(text) >= max_text_tag:
                block["text"] = _TAG_PATTERN.sub(
                    lambda m: _truncate_tag(m, max_text_tag),
                    text,
                )
        elif btype == "thinking":
            block["text"] = _truncate_text(block.get("text", ""), _MAX_THINKING)
        elif btype == "image":
            block.clear()
            block.update({"type": "text", "text": "[image omitted by microcompact]"})
        elif btype == "tool_use":
            args = block.get("arguments", "")
            if isinstance(args, str):
                block["arguments"] = _truncate_text(args, _MAX_TOOL_ARGS)
        elif btype == "tool_result":
            content = block.get("content", "")
            if isinstance(content, str):
                block["content"] = _truncate_text(content, _MAX_TOOL_CONTENT)


def _split_for_full_compact(history: list[dict], keep_recent_turns: int) -> int:
    user_indices = [
        i for i, msg in enumerate(history)
        if msg.get("role") == "user" and not _only_tool_results(msg)
    ]
    if len(user_indices) <= keep_recent_turns + 1:
        return 0
    keep_from = user_indices[-keep_recent_turns]
    return max(0, keep_from)


def _first_turn_end(history: list[dict]) -> int:
    if len(history) <= 1:
        return len(history)
    for i in range(1, len(history)):
        if history[i].get("role") == "user" and not _only_tool_results(history[i]):
            return i
    return min(len(history), 1)


def _summarize_messages(messages: list[dict]) -> str:
    facts: list[str] = []
    tool_names: list[str] = []
    identifiers: list[str] = []
    errors: list[str] = []

    for msg in messages:
        role = msg.get("role", "?")
        text = _message_text(msg)
        if text:
            facts.append(f"- {role}: {_truncate_text(text, 220)}")
            identifiers.extend(_extract_identifiers(text))
            if _looks_error(text):
                errors.append(_truncate_text(text, 180))
        for block in msg.get("blocks", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = str(block.get("name", "")).strip()
                if name:
                    tool_names.append(name)
                identifiers.extend(_extract_identifiers(str(block.get("arguments", ""))))
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                content = str(block.get("content", ""))
                identifiers.extend(_extract_identifiers(content))
                if _looks_error(content):
                    errors.append(_truncate_text(content, 180))

    sections = ["<earlier_summary>", "Earlier conversation was compacted. Preserve these identifiers and decisions."]
    if facts:
        sections.append("Key turns:")
        sections.extend(facts[-24:])
    unique_ids = _unique_keep_order(identifiers)[:40]
    if unique_ids:
        sections.append("Identifiers:")
        sections.append(", ".join(unique_ids))
    unique_tools = _unique_keep_order(tool_names)[:20]
    if unique_tools:
        sections.append("Tools used:")
        sections.append(", ".join(unique_tools))
    unique_errors = _unique_keep_order(errors)[:8]
    if unique_errors:
        sections.append("Errors / blockers:")
        sections.extend(f"- {item}" for item in unique_errors)
    sections.append("</earlier_summary>")
    return "\n".join(sections)


def _message_text(msg: dict) -> str:
    parts: list[str] = []
    for block in msg.get("blocks", []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif block.get("type") == "tool_result":
            parts.append(str(block.get("content", "")))
    return "\n".join(p for p in parts if p).strip()


def _extract_identifiers(text: str) -> list[str]:
    patterns = [
        r"[A-Za-z]:\\[^\s\"'<>|]+",
        r"(?:[\w.-]+/)+[\w.-]+",
        r"\b[\w.-]+\.(?:py|md|json|toml|txt|yaml|yml|js|ts|tsx|jsx|css|html)\b",
        r"\b(?:pytest|python|git|npm|uv|pip)\s+[^\n\r]{1,100}",
        r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b",
    ]
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, text))
    return [str(item).strip(".,;:()[]{}") for item in found if str(item).strip()]


def _looks_error(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ("error", "exception", "traceback", "failed", "失败", "报错"))


def _only_tool_results(msg: dict) -> bool:
    blocks = msg.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return False
    return all(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks)


def _tool_use_ids(msg: dict | None) -> set[str]:
    if not msg or msg.get("role") != "assistant":
        return set()
    return {
        str(block.get("id", ""))
        for block in msg.get("blocks", [])
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")
    }


def _tool_result_ids(msg: dict | None) -> set[str]:
    if not msg or msg.get("role") != "user":
        return set()
    return {
        str(block.get("tool_use_id", ""))
        for block in msg.get("blocks", [])
        if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id")
    }


def _strip_tool_uses(msg: dict, missing_ids: set[str]) -> None:
    if not missing_ids:
        return
    blocks = msg.get("blocks")
    if not isinstance(blocks, list):
        return
    msg["blocks"] = [
        block for block in blocks
        if not (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("id") in missing_ids
        )
    ]


def _convert_orphan_tool_results(msg: dict, valid_ids: set[str]) -> None:
    blocks = msg.get("blocks")
    if not isinstance(blocks, list):
        return
    cleaned: list[dict] = []
    for block in blocks:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_result"
            and block.get("tool_use_id") not in valid_ids
        ):
            text = str(block.get("content", ""))
            if text:
                cleaned.append({
                    "type": "text",
                    "text": f"[orphaned tool result converted to text]\n{text[:400]}",
                })
            continue
        cleaned.append(block)
    msg["blocks"] = cleaned


def _has_blocks(msg: dict) -> bool:
    blocks = msg.get("blocks")
    return not isinstance(blocks, list) or bool(blocks)


def _truncate_tag(match: re.Match, max_len: int) -> str:
    tag, body = match.group(1), match.group(2)
    if len(body) <= max_len:
        return match.group(0)
    return f"<{tag}>{_truncate_text(body, max_len)}</{tag}>"


def _truncate_text(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    marker = "...[Truncated]..."
    keep = max(0, max_len - len(marker))
    head = keep // 2
    tail = keep - head
    return text[:head] + marker + text[-tail:]


def _unique_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _calc_cost(history: list[dict]) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in history)
