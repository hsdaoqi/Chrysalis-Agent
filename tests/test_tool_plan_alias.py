from pathlib import Path

import chrysalis.tools  # noqa: F401
from chrysalis.permission import PermissionEngine
from chrysalis.tools.registry import generate_tools_schema, get_registry, run_tool


def test_todo_write_is_exposed_and_plan_write_is_removed() -> None:
    registry = get_registry()
    schema_names = {tool["function"]["name"] for tool in generate_tools_schema()}

    assert "todo_write" in registry
    assert "plan_write" not in registry
    assert "todo_write" in schema_names
    assert "plan_write" not in schema_names


def test_todo_write_runs_todo_tool() -> None:
    result = run_tool(
        "todo_write",
        {
            "goal": "Migrate planning back to TODOs",
            "items": ["Remove old tool exposure", "Update TODO state"],
            "action": "set",
        },
        Path.cwd(),
    )

    assert result["ok"] is True
    assert result["_todo"] is True
    assert result["todo_action"] == "set"
    assert result["goal"] == "Migrate planning back to TODOs"
    assert result["todos"] == ["Remove old tool exposure", "Update TODO state"]


def test_permission_accepts_todo_write(tmp_path: Path) -> None:
    engine = PermissionEngine(level="balanced", store_path=tmp_path / "permissions.json")

    decision = engine.assess_tool("todo_write", {"items": ["Keep compatibility"]}, workspace=tmp_path)

    assert decision.decision == "allow"
    assert decision.tool == "todo_write"


def test_plan_write_is_unknown(tmp_path: Path) -> None:
    result = run_tool("plan_write", {"steps": ["legacy"]}, tmp_path)

    assert result["ok"] is False
    assert "unknown" in result["error"].lower() or "未知" in result["error"]
