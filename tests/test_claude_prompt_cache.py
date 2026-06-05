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


def test_session_config_coerces_prompt_cache_flag() -> None:
    config = SessionConfig(
        api_key="",
        base_url="https://example.invalid",
        model="claude-test",
        protocol="anthropic",
        prompt_cache_enabled="false",
    )

    assert config.prompt_cache_enabled is False
