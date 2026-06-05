from chrysalis.llm.context import CompactionManager, drop_non_a_messages, microcompact_history, trim_messages_history
from chrysalis.llm.types import SessionConfig


def _msg(role: str, text: str, level: str | None = None) -> dict:
    msg = {"role": role, "blocks": [{"type": "text", "text": text}]}
    if level:
        msg["_compact_level"] = level
    return msg


def test_drop_non_a_messages_removes_every_compacted_layer() -> None:
    history = [
        _msg("user", "raw-1"),
        _msg("assistant", "micro-1", "micro"),
        _msg("user", "snip-1", "snip"),
        _msg("assistant", "full-1", "full"),
        _msg("user", "raw-2"),
    ]

    dropped = drop_non_a_messages(history)

    assert dropped == 3
    assert [msg["blocks"][0]["text"] for msg in history] == ["raw-1", "raw-2"]
    assert all(msg.get("_compact_level", "raw") == "raw" for msg in history)


def test_trim_messages_history_batch_drops_compacted_prefix() -> None:
    history = [
        _msg("user", "raw-head"),
        _msg("assistant", "micro-mid", "micro"),
        _msg("user", "snip-mid", "snip"),
        _msg("assistant", "full-mid", "full"),
        _msg("user", "raw-tail"),
    ]

    trim_messages_history(history, context_window=1)

    assert [msg["blocks"][0]["text"] for msg in history] == ["raw-head", "raw-tail"]
    assert all(msg.get("_compact_level", "raw") == "raw" for msg in history)


def test_preflight_keeps_compacted_memory_until_hard_limit() -> None:
    config = SessionConfig(
        api_key="",
        base_url="https://example.invalid",
        model="test-model",
        context_window=1000,
        compression_soft_limit_ratio=0.10,
        compression_hard_limit_ratio=0.95,
    )
    manager = CompactionManager(config)
    history = [
        _msg("user", "important compacted decision", "full"),
        _msg("user", "x" * 500),
    ]

    stats = manager.apply_preflight(history)

    assert stats.after_chars > manager.soft_budget_chars
    assert stats.after_chars < manager.hard_budget_chars
    assert "important compacted decision" in history[0]["blocks"][0]["text"]
    assert history[0].get("_compact_level") == "full"


def test_microcompact_does_not_prune_duplicate_tool_output_in_protected_tail() -> None:
    duplicate = "tool output " * 40
    history = [
        _msg("user", "old turn"),
        {
            "role": "assistant",
            "blocks": [{"type": "tool_use", "id": "old", "name": "file_read", "arguments": "{}"}],
        },
        {
            "role": "user",
            "blocks": [{"type": "tool_result", "tool_use_id": "old", "content": duplicate}],
        },
        _msg("assistant", "old done"),
        _msg("user", "recent turn one"),
        {
            "role": "assistant",
            "blocks": [{"type": "tool_use", "id": "recent", "name": "file_read", "arguments": "{}"}],
        },
        {
            "role": "user",
            "blocks": [{"type": "tool_result", "tool_use_id": "recent", "content": duplicate}],
        },
        _msg("user", "recent turn two"),
    ]

    microcompact_history(history, keep_recent=2)

    assert history[6]["blocks"][0]["content"] == duplicate
    assert history[2]["blocks"][0]["content"] == "[Duplicate tool output: same content as a more recent call]"
