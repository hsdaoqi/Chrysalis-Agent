from pathlib import Path

from chrysalis.permission import PermissionEngine


def test_full_level_allows_mutating_tool(tmp_path: Path) -> None:
    engine = PermissionEngine(level="full", store_path=tmp_path / "permissions.json")

    decision = engine.assess_tool(
        "file_write",
        {"path": "note.txt", "content": "hello"},
        workspace=tmp_path,
    )

    assert decision.decision == "allow"


def test_balanced_level_asks_for_file_write(tmp_path: Path) -> None:
    engine = PermissionEngine(level="balanced", store_path=tmp_path / "permissions.json")

    decision = engine.assess_tool(
        "file_write",
        {"path": "note.txt", "content": "hello"},
        workspace=tmp_path,
    )

    result = decision.to_result()
    assert decision.decision == "ask"
    assert result["permission_request"] is True
    assert any(label.startswith("允许本次") for label in result["candidates"])
    assert any(label.startswith("永久允许") for label in result["candidates"])


def test_persistent_grant_allows_same_operation(tmp_path: Path) -> None:
    store_path = tmp_path / "permissions.json"
    args = {"path": "note.txt", "content": "hello"}
    engine = PermissionEngine(level="balanced", store_path=store_path)
    request = engine.assess_tool("file_write", args, workspace=tmp_path).to_result()

    resolved = engine.resolve_user_choice(request, "永久允许同类操作")
    assert resolved["action"] == "allow"

    reloaded = PermissionEngine(level="balanced", store_path=store_path)
    decision = reloaded.assess_tool("file_write", args, workspace=tmp_path)
    assert decision.decision == "allow"
    assert decision.reason == "matched a persistent permission grant"


def test_one_time_grant_is_consumed(tmp_path: Path) -> None:
    args = {"path": "note.txt", "content": "hello"}
    engine = PermissionEngine(level="balanced", store_path=tmp_path / "permissions.json")
    request = engine.assess_tool("file_write", args, workspace=tmp_path).to_result()

    resolved = engine.resolve_user_choice(request, "允许本次")
    assert resolved["action"] == "allow"

    first = engine.assess_tool("file_write", args, workspace=tmp_path)
    second = engine.assess_tool("file_write", args, workspace=tmp_path)
    assert first.decision == "allow"
    assert first.reason == "matched a one-time permission grant"
    assert second.decision == "ask"


def test_sensitive_file_read_requires_confirmation(tmp_path: Path) -> None:
    secret = tmp_path / ".env"
    secret.write_text("TOKEN=secret", encoding="utf-8")
    engine = PermissionEngine(level="balanced", store_path=tmp_path / "permissions.json")

    decision = engine.assess_tool("file_read", {"path": str(secret)}, workspace=tmp_path)

    assert decision.decision == "ask"
    assert decision.risk == "high"
