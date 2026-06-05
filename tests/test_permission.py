from pathlib import Path

from chrysalis.permission import GatewayPermissionEngine, PermissionEngine


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


def test_one_time_grant_can_resume_from_serialized_result(tmp_path: Path) -> None:
    args = {"path": "note.txt", "content": "hello"}
    store_path = tmp_path / "permissions.json"
    request = PermissionEngine(level="balanced", store_path=store_path).assess_tool(
        "file_write",
        args,
        workspace=tmp_path,
    ).to_result()

    resumed = PermissionEngine(level="balanced", store_path=store_path)
    resolved = resumed.resolve_user_choice(request, "allow_once")

    assert resolved["action"] == "allow"
    assert resumed.assess_tool("file_write", args, workspace=tmp_path).decision == "allow"
    assert resumed.assess_tool("file_write", args, workspace=tmp_path).decision == "ask"


def test_persistent_grant_can_resume_from_serialized_result(tmp_path: Path) -> None:
    args = {"path": "note.txt", "content": "hello"}
    store_path = tmp_path / "permissions.json"
    request = PermissionEngine(level="balanced", store_path=store_path).assess_tool(
        "file_write",
        args,
        workspace=tmp_path,
    ).to_result()

    resumed = PermissionEngine(level="balanced", store_path=store_path)
    resolved = resumed.resolve_user_choice(request, "allow_always")

    assert resolved["action"] == "allow"
    reloaded = PermissionEngine(level="balanced", store_path=store_path)
    assert reloaded.assess_tool("file_write", args, workspace=tmp_path).decision == "allow"


def test_sensitive_file_read_requires_confirmation(tmp_path: Path) -> None:
    secret = tmp_path / ".env"
    secret.write_text("TOKEN=secret", encoding="utf-8")
    engine = PermissionEngine(level="balanced", store_path=tmp_path / "permissions.json")

    decision = engine.assess_tool("file_read", {"path": str(secret)}, workspace=tmp_path)

    assert decision.decision == "ask"
    assert decision.risk == "high"


def test_gateway_permission_denies_local_tool_access(tmp_path: Path) -> None:
    engine = GatewayPermissionEngine()

    decision = engine.assess_tool(
        "code_run",
        {"script": "print(1)"},
        workspace=tmp_path,
    )

    assert decision.decision == "deny"
    assert decision.risk == "high"
    assert decision.reason == "remote gateway first principle denied host-machine access"


def test_gateway_permission_allows_safe_internal_tools(tmp_path: Path) -> None:
    engine = GatewayPermissionEngine()

    decision = engine.assess_tool(
        "todo_write",
        {"items": "[]"},
        workspace=tmp_path,
    )

    assert decision.decision == "allow"


def test_gateway_permission_cannot_be_approved_by_remote_user(tmp_path: Path) -> None:
    secret = tmp_path / ".env"
    secret.write_text("TOKEN=secret", encoding="utf-8")
    engine = GatewayPermissionEngine(allowed_tools={"file_read"})

    decision = engine.assess_tool("file_read", {"path": str(secret)}, workspace=tmp_path)
    resolved = engine.resolve_user_choice(decision.to_result(), "allow_once")

    assert decision.decision == "deny"
    assert decision.reason == "remote gateway first principle denied host file access"
    assert resolved["action"] == "deny"


def test_gateway_permission_allows_public_web_fetch(tmp_path: Path) -> None:
    engine = GatewayPermissionEngine()

    decision = engine.assess_tool(
        "web_fetch",
        {"url": "https://example.com"},
        workspace=tmp_path,
    )

    assert decision.decision == "allow"
    assert decision.reason == "remote gateway public web fetch"


def test_gateway_permission_denies_private_web_fetch(tmp_path: Path) -> None:
    engine = GatewayPermissionEngine()

    decision = engine.assess_tool(
        "web_fetch",
        {"url": "http://127.0.0.1:8000"},
        workspace=tmp_path,
    )

    assert decision.decision == "deny"
    assert decision.reason == "remote gateway first principle denied local or private network access"


def test_gateway_permission_denies_browser_tools(tmp_path: Path) -> None:
    engine = GatewayPermissionEngine()

    decision = engine.assess_tool(
        "web_scan",
        {"url": "https://example.com"},
        workspace=tmp_path,
    )

    assert decision.decision == "deny"
    assert decision.reason == "remote gateway first principle denied host-machine access"


def test_gateway_permission_file_read_is_limited_to_allowed_roots(tmp_path: Path) -> None:
    gateway_dir = tmp_path / "gateway-media"
    gateway_dir.mkdir()
    allowed_file = gateway_dir / "note.txt"
    allowed_file.write_text("hello", encoding="utf-8")
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret", encoding="utf-8")
    engine = GatewayPermissionEngine(allowed_read_roots=[gateway_dir])

    allowed = engine.assess_tool("file_read", {"path": str(allowed_file)}, workspace=tmp_path)
    denied = engine.assess_tool("file_read", {"path": str(outside_file)}, workspace=tmp_path)

    assert allowed.decision == "allow"
    assert denied.decision == "deny"
    assert denied.reason == "remote gateway first principle denied host file access"
