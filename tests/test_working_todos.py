from chrysalis.working import WorkingMemory


def test_completed_todos_move_to_bottom() -> None:
    working = WorkingMemory()
    working.update_todos(
        [
            {"id": "a", "title": "A"},
            {"id": "b", "title": "B"},
            {"id": "c", "title": "C"},
        ],
        action="set",
    )

    working.update_todos([{"id": "b", "title": "B"}], action="complete")

    snapshot = working.todo_snapshot()
    assert [item["id"] for item in snapshot["todos"]] == ["a", "c", "b"]
    assert snapshot["active_todo_id"] == "a"


def test_update_preserves_completed_items_at_bottom() -> None:
    working = WorkingMemory()
    working.update_todos(
        [
            {"id": "a", "title": "A"},
            {"id": "b", "title": "B", "status": "completed"},
            {"id": "c", "title": "C"},
        ],
        action="set",
    )

    snapshot = working.todo_snapshot()
    assert [item["id"] for item in snapshot["todos"]] == ["a", "c", "b"]
