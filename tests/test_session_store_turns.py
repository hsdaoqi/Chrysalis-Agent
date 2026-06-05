from pathlib import Path

from chrysalis.session_store import SessionStore


def _text(role: str, text: str) -> dict:
    return {"role": role, "blocks": [{"type": "text", "text": text}]}


def _tool_result() -> dict:
    return {"role": "user", "blocks": [{"type": "tool_result", "content": "ok"}]}


def test_session_turns_count_user_requests_not_history_messages(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.new_session(model="test-model")
    store.save([
        _text("user", "first"),
        _text("assistant", "working"),
        _tool_result(),
        _text("assistant", "done"),
        _text("user", "second"),
    ])

    sessions = store.list_sessions()

    assert sessions[0]["turns"] == 2


def test_list_sessions_recomputes_legacy_turn_counts(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session_id = store.new_session(model="test-model")
    store.save([_text("user", "first"), _text("assistant", "done")])
    path = tmp_path / f"{session_id}.json"
    data = path.read_text(encoding="utf-8")
    path.write_text(data.replace('"turns": 1', '"turns": 2'), encoding="utf-8")

    sessions = store.list_sessions()

    assert sessions[0]["turns"] == 1
