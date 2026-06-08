from chrysalis.llm.claude_stream import _build_payload
from chrysalis.llm.types import SessionConfig


def _config(prompt_cache_enabled: bool = True) -> SessionConfig:
    return SessionConfig(
        api_key="",
        base_url="https://example.invalid",
        model="claude-test",
        protocol="anthropic",
        prompt_cache_enabled=prompt_cache_enabled,
    )


def test_claude_payload_caches_stable_system_prefix_only() -> None:
    system = "base system\n\n## Stable Context\nstable memory\n\n## Runtime Context\nvolatile task state"

    payload = _build_payload(_config(), [], system, None)

    assert payload["system"] == [
        {
            "type": "text",
            "text": "base system\n\n## Stable Context\nstable memory",
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": "## Runtime Context\nvolatile task state"},
    ]


def test_claude_payload_caches_tools_without_mutating_schema() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "read a file",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    payload = _build_payload(_config(), [], "base system", tools)

    assert payload["tools"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in tools[0]


def test_claude_prompt_cache_can_be_disabled() -> None:
    payload = _build_payload(_config(prompt_cache_enabled=False), [], "base system", [])

    assert payload["system"] == "base system"


def test_claude_payload_caches_message_history_tail() -> None:
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "do the thing"}]},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "read", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
        },
    ]

    payload = _build_payload(_config(), messages, "base system", None)

    # 断点挂在最后一条 message 的最后一个 block 上
    assert payload["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    # 历史前缀的其它消息不挂断点
    assert "cache_control" not in payload["messages"][0]["content"][-1]
    assert "cache_control" not in payload["messages"][1]["content"][-1]


def test_claude_message_cache_does_not_mutate_input() -> None:
    messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]

    _build_payload(_config(), messages, "base system", None)

    assert "cache_control" not in messages[0]["content"][0]


def test_claude_message_cache_disabled_leaves_messages_untouched() -> None:
    messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]

    payload = _build_payload(_config(prompt_cache_enabled=False), messages, "base system", None)

    assert "cache_control" not in payload["messages"][0]["content"][0]


def test_claude_message_cache_skips_non_list_content() -> None:
    # 防御：content 不是 block 列表时不应抛错，也不挂断点
    messages = [{"role": "user", "content": "plain string"}]

    payload = _build_payload(_config(), messages, "base system", None)

    assert payload["messages"][0]["content"] == "plain string"


def test_session_config_coerces_prompt_cache_flag() -> None:
    config = SessionConfig(
        api_key="",
        base_url="https://example.invalid",
        model="claude-test",
        protocol="anthropic",
        prompt_cache_enabled="false",
    )

    assert config.prompt_cache_enabled is False
