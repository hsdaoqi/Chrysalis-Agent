from pathlib import Path

from chrysalis.llm.context import (
    CompactionManager,
    estimate_request_cost,
    full_compact_history,
    microcompact_history,
    repair_tool_pairs,
    snip_compact_history,
)
from chrysalis.kernel import format_context_usage
from chrysalis.llm.client import LLMClient
from chrysalis.llm.protocols import to_openai_messages
from chrysalis.llm.session import BaseSession
from chrysalis.llm.types import Response, SessionConfig


def _config(**overrides) -> SessionConfig:
    values = {
        "api_key": "test",
        "base_url": "http://example.invalid",
        "model": "test-model",
        "context_window": 100_000,
    }
    values.update(overrides)
    return SessionConfig(**values)


def _tool_turn(i: int, content: str = "ok") -> list[dict]:
    tid = f"tool-{i}"
    return [
        {
            "role": "assistant",
            "blocks": [{"type": "tool_use", "id": tid, "name": "file_read", "arguments": "{}"}],
        },
        {
            "role": "user",
            "blocks": [{"type": "tool_result", "tool_use_id": tid, "content": content}],
        },
    ]


def _assert_canonical_tool_pairs(history: list[dict]) -> None:
    for idx, msg in enumerate(history):
        if msg.get("role") != "assistant":
            continue
        ids = {
            block.get("id")
            for block in msg.get("blocks", [])
            if block.get("type") == "tool_use"
        }
        if not ids:
            continue
        next_msg = history[idx + 1] if idx + 1 < len(history) else {}
        answered = {
            block.get("tool_use_id")
            for block in next_msg.get("blocks", [])
            if block.get("type") == "tool_result"
        }
        assert ids <= answered


def test_tool_result_budget_archives_large_latest_result(tmp_path: Path):
    history = [
        {
            "role": "assistant",
            "blocks": [{"type": "tool_use", "id": "call-1", "name": "code_run", "arguments": "{}"}],
        },
        {
            "role": "user",
            "blocks": [{"type": "tool_result", "tool_use_id": "call-1", "content": "x" * 260_000}],
        },
    ]
    manager = CompactionManager(
        _config(context_window=80),
        output_dir=tmp_path / "tool_results",
        transcript_dir=tmp_path / "transcripts",
    )

    stats = manager.apply_preflight(history)

    content = history[-1]["blocks"][0]["content"]
    assert stats.tool_results_archived == 1
    assert "tool_result archived by tool_result_budget" in content
    assert "archive_path:" in content
    assert Path(history[-1]["blocks"][0]["_archived_path"]).exists()
    _assert_canonical_tool_pairs(history)


def test_preflight_does_not_compact_before_seventy_percent(tmp_path: Path):
    history = [
        {"role": "user", "blocks": [{"type": "text", "text": "start"}]},
        {"role": "assistant", "blocks": [{"type": "text", "text": "ok"}]},
        {"role": "user", "blocks": [{"type": "text", "text": "x" * 1_000}]},
    ]
    before = len(history)
    manager = CompactionManager(
        _config(context_window=1_000),
        output_dir=tmp_path / "tool_results",
        transcript_dir=tmp_path / "transcripts",
    )

    stats = manager.apply_preflight(history, system="small", tools=[])

    assert len(history) == before
    assert not stats.micro_compacted
    assert not stats.full_compacted
    assert estimate_request_cost(history, system="small", tools=[]) < manager.soft_budget_chars


def test_preflight_counts_system_and_tools_for_seventy_percent(tmp_path: Path):
    history = []
    for i in range(12):
        history.append({"role": "user", "blocks": [{"type": "text", "text": f"task {i}"}]})
        history.append({"role": "assistant", "blocks": [{"type": "text", "text": "done"}]})
    manager = CompactionManager(
        _config(context_window=90),
        output_dir=tmp_path / "tool_results",
        transcript_dir=tmp_path / "transcripts",
    )
    system = "system prompt " + ("x" * 260)

    stats = manager.apply_preflight(history, system=system, tools=[])

    assert stats.full_compacted or stats.micro_compacted or stats.snip_compacted
    assert history[-1]["blocks"][0]["text"] == "done"


def test_snip_compact_preserves_tool_pairs_and_openai_wire_validity():
    history = [{"role": "user", "blocks": [{"type": "text", "text": "start"}]}]
    for i in range(20):
        history.extend(_tool_turn(i))
        history.append({"role": "user", "blocks": [{"type": "text", "text": f"continue {i}"}]})

    assert snip_compact_history(history, keep_recent_turns=4, max_messages=12)
    repair_tool_pairs(history)

    _assert_canonical_tool_pairs(history)
    wire = to_openai_messages(history)
    pending = set()
    for msg in wire:
        if msg.get("role") == "assistant":
            pending = {tc["id"] for tc in msg.get("tool_calls", [])}
        elif msg.get("role") == "tool":
            assert msg["tool_call_id"] in pending
            pending.remove(msg["tool_call_id"])


def test_microcompact_preserves_spawn_subagent_result():
    huge_result = "subagent summary " + ("important " * 700)
    history = [
        {
            "role": "assistant",
            "blocks": [{
                "type": "tool_use",
                "id": "agent-1",
                "name": "spawn_subagent",
                "arguments": '{"task": "investigate"}',
            }],
        },
        {
            "role": "user",
            "blocks": [{"type": "tool_result", "tool_use_id": "agent-1", "content": huge_result}],
        },
        {
            "role": "assistant",
            "blocks": [{
                "type": "tool_use",
                "id": "read-1",
                "name": "file_read",
                "arguments": '{"path": "README.md"}',
            }],
        },
        {
            "role": "user",
            "blocks": [{"type": "tool_result", "tool_use_id": "read-1", "content": "x" * 6_000}],
        },
    ]

    assert microcompact_history(history, keep_recent=0, force=True)

    assert history[1]["blocks"][0]["content"] == huge_result
    assert "pruned old tool output" in history[3]["blocks"][0]["content"]


def test_microcompact_uses_tail_token_budget_to_protect_more_than_fixed_turns():
    history = [
        {
            "role": "assistant",
            "blocks": [{"type": "tool_use", "id": "read-1", "name": "file_read", "arguments": '{"path": "a.txt"}'}],
        },
        {
            "role": "user",
            "blocks": [{"type": "tool_result", "tool_use_id": "read-1", "content": "old output " * 500}],
        },
        {"role": "user", "blocks": [{"type": "text", "text": "latest"}]},
    ]

    changed = microcompact_history(history, keep_recent=1, protect_tail_tokens=10_000)

    assert not changed
    assert history[1]["blocks"][0]["content"].startswith("old output")


def test_context_usage_reports_current_history_budget():
    session = BaseSession(_config(context_window=100))
    client = LLMClient(session)
    session.system = "system"
    session.history.extend([
        {"role": "user", "blocks": [{"type": "text", "text": "hello"}]},
        {"role": "assistant", "blocks": [{"type": "text", "text": "world"}]},
    ])

    usage = client.context_usage()

    assert usage["messages"] == 2
    assert usage["context_window"] == 100
    assert usage["budget_chars"] == 300
    assert usage["chars"] == estimate_request_cost(session.history, system="system", tools=None)
    assert usage["blocks"]["text"] == 2
    assert 0 < usage["ratio"] <= 1


def test_format_context_usage_draws_progress_bar():
    line = format_context_usage({
        "chars": 150,
        "budget_chars": 300,
        "tokens_estimate": 50,
        "context_window": 100,
        "messages": 4,
        "soft_ratio": 0.7,
        "hard_ratio": 0.9,
        "last_compaction": {"micro": True, "tool_results_archived": 1},
    }, width=10)

    assert "Context [" in line
    assert "50%" in line
    assert "50/100 tok" in line
    assert "4 msgs" in line
    assert "compact: micro,archived 1" in line


def test_full_compact_accepts_llm_summary_text():
    history = []
    for i in range(6):
        history.append({
            "role": "user",
            "blocks": [{"type": "text", "text": f"<earlier_summary>task {i} src/file_{i}.py</earlier_summary>"}],
            "_compact_level": "snip",
            "_snip_compact": True,
        })
    history.append({"role": "user", "blocks": [{"type": "text", "text": "latest task"}]})

    assert full_compact_history(
        history,
        target_chars=2_000,
        keep_recent_turns=1,
        summary_text="<earlier_summary>\nUser Goal: keep exact path src/main.py\n</earlier_summary>",
    )

    assert history[0]["_full_compact"] is True
    assert "src/main.py" in history[0]["blocks"][0]["text"]
    assert history[0]["_compact_level"] == "full"
    assert history[-1]["blocks"][0]["text"] == "latest task"


def test_full_compact_prefers_latest_identifiers():
    history = []
    for i in range(55):
        history.append({
            "role": "user",
            "blocks": [{"type": "text", "text": f"touch src/file_{i}.py IDENT_{i}"}],
            "_compact_level": "snip",
            "_snip_compact": True,
        })
    history.append({"role": "user", "blocks": [{"type": "text", "text": "latest"}]})

    assert full_compact_history(history, target_chars=2_000, keep_recent_turns=1)

    summary = history[0]["blocks"][0]["text"]
    assert "IDENT_0" not in summary
    assert "IDENT_50" in summary


def test_full_compact_appends_new_d_without_rewriting_old_d():
    old_d_text = "<earlier_summary>\nD1 stable\n</earlier_summary>"
    history = [
        {
            "role": "user",
            "blocks": [{"type": "text", "text": "head"}],
        },
        {
            "role": "user",
            "blocks": [{"type": "text", "text": old_d_text}],
            "_compact_level": "full",
            "_full_compact": True,
        },
    ]
    for i in range(4):
        history.append({
            "role": "user",
            "blocks": [{"type": "text", "text": f"<earlier_summary>C{i} new segment</earlier_summary>"}],
            "_compact_level": "snip",
            "_snip_compact": True,
        })
    history.append({"role": "user", "blocks": [{"type": "text", "text": "tail"}]})

    assert full_compact_history(history, target_chars=2_000, keep_recent_turns=1)

    full_blocks = [m for m in history if m.get("_compact_level") == "full"]
    assert len(full_blocks) == 2
    assert full_blocks[0]["blocks"][0]["text"] == old_d_text
    assert "C3 new segment" in full_blocks[1]["blocks"][0]["text"]


class _PromptTooLongSession(BaseSession):
    def __init__(self, config: SessionConfig, tmp_path: Path):
        super().__init__(config)
        self.calls = 0
        self.compaction.output_dir = tmp_path / "tool_results"
        self.compaction.transcript_dir = tmp_path / "transcripts"

    def _raw_ask(self, history, cancel_event=None):
        self.calls += 1
        if self.calls == 1:
            text = "!!!Error: context length exceeded"
            yield text
            return Response(content=text, raw=text)
        yield "ok"
        return Response(content="ok", raw="ok")


def test_reactive_compact_retries_context_limit_once(tmp_path: Path):
    session = _PromptTooLongSession(_config(context_window=80), tmp_path)
    message = {
        "role": "user",
        "blocks": [{"type": "text", "text": "please continue " + ("x" * 500)}],
    }
    gen = session.ask(message)
    chunks = []
    try:
        while True:
            chunks.append(next(gen))
    except StopIteration as exc:
        response = exc.value

    assert session.calls == 2
    assert response.content == "ok"
    assert any("已自动压缩历史并重试一次" in chunk for chunk in chunks)
    assert list((tmp_path / "transcripts").glob("*.json"))
