"""Helpers for displaying canonical LLM history in user-facing UIs."""

from __future__ import annotations

import re
from typing import Any

ORPHANED_TOOL_RESULT_PREFIX = "[orphaned tool result converted to text]"

_INTERNAL_PROMPT_KINDS = {
    "internal",
    "internal_prompt",
    "repair_prompt",
    "continue_prompt",
    "tool_followup",
}
_INTERNAL_ASSISTANT_KINDS = {
    "internal",
    "internal_candidate",
    "repair_candidate",
}
_INTERNAL_PROMPT_PREFIXES = (
    "请继续",
)


def message_meta(message: dict[str, Any]) -> dict[str, Any]:
    meta = message.get("meta")
    return meta if isinstance(meta, dict) else {}


def message_ui_kind(message: dict[str, Any]) -> str:
    meta = message_meta(message)
    ui = meta.get("ui")
    if isinstance(ui, dict):
        kind = str(ui.get("kind", "")).strip().lower()
        if kind:
            return kind
    return str(meta.get("kind", "")).strip().lower()


def message_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = message.get("blocks", [])
    if not isinstance(blocks, list):
        return []
    return [block for block in blocks if isinstance(block, dict)]


def extract_text(blocks: list[dict[str, Any]]) -> str:
    parts = [
        str(block.get("text", ""))
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    ]
    return "".join(parts).strip()


def message_display_text(message: dict[str, Any]) -> str:
    meta = message_meta(message)
    ui = meta.get("ui")
    if isinstance(ui, dict):
        text = str(ui.get("display_text") or "").strip()
        if text:
            return text
    return str(meta.get("display_text") or "").strip()


def visible_user_text(message: dict[str, Any]) -> str:
    text = message_display_text(message) or extract_text(message_blocks(message))
    text = strip_runtime_context(text)
    return "" if is_orphaned_tool_result_text(text) else text


def strip_runtime_context(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return ""

    markers = (
        "## Runtime Context",
        "# Runtime Context",
        "[Relevant Skills]",
        "[Relevant L2/L3]",
        "[Runtime Continuation]",
        "### [SESSION CONTEXT]",
        "# [SESSION CONTEXT]",
        "[SESSION CONTEXT]",
        "<recent_turns>",
        "</recent_turns>",
        "## 褰撳墠鐭湡宸ヤ綔璁板繂",
        "### 褰撳墠鐭湡宸ヤ綔璁板繂",
    )
    cut_index = -1
    for marker in markers:
        index = normalized.find(marker)
        if index >= 0 and (cut_index < 0 or index < cut_index):
            cut_index = index
    if cut_index >= 0:
        normalized = normalized[:cut_index]
    return normalized.strip()


def extract_thinking(blocks: list[dict[str, Any]]) -> str:
    parts = [
        str(block.get("text", "")).strip()
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "thinking" and str(block.get("text", "")).strip()
    ]
    return "\n\n".join(parts).strip()


def has_tool_result(blocks: list[dict[str, Any]]) -> bool:
    return any(isinstance(block, dict) and block.get("type") == "tool_result" for block in blocks)


def has_tool_use(blocks: list[dict[str, Any]]) -> bool:
    return any(isinstance(block, dict) and block.get("type") == "tool_use" for block in blocks)


def extract_tool_names(blocks: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        name = str(block.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def normalize_final_text(text: str) -> str:
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<summary>.*?</summary>", "", text, flags=re.I | re.S)
    text = re.sub(r"</?summary>", "", text, flags=re.I)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact_text(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def is_internal_prompt_text(text: str) -> bool:
    normalized = str(text).strip()
    if not normalized:
        return False
    first_line = normalized.splitlines()[0].strip()
    if first_line.startswith(_INTERNAL_PROMPT_PREFIXES):
        return True
    if first_line.startswith("JSON ") and "final JSON" in normalized:
        return True
    return False


def is_internal_prompt_message(message: dict[str, Any]) -> bool:
    if str(message.get("role", "")) != "user":
        return False
    if message_ui_kind(message) in _INTERNAL_PROMPT_KINDS:
        return True
    return is_internal_prompt_text(extract_text(message_blocks(message)))


def is_tool_result_message(message: dict[str, Any]) -> bool:
    return str(message.get("role", "")) == "user" and has_tool_result(message_blocks(message))


def is_orphaned_tool_result_text(text: str) -> bool:
    return str(text or "").strip().startswith(ORPHANED_TOOL_RESULT_PREFIX)


def is_internal_assistant_candidate(history: list[dict[str, Any]], index: int) -> bool:
    if index < 0 or index >= len(history):
        return False
    message = history[index]
    if str(message.get("role", "")) != "assistant":
        return False
    if message_ui_kind(message) in _INTERNAL_ASSISTANT_KINDS:
        return True
    if index + 1 >= len(history):
        return False
    return is_internal_prompt_message(history[index + 1])
