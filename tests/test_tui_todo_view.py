from chrysalis.tui.app import _compact_todo_items


def _items(count: int) -> list[dict]:
    return [{"id": str(i), "title": f"{i}任务", "status": "pending"} for i in range(1, count + 1)]


def test_compact_todo_items_keeps_head_and_tail() -> None:
    visible, hidden = _compact_todo_items(_items(7))

    assert hidden == 3
    assert [item["id"] if item else "..." for item in visible] == ["1", "2", "...", "6", "7"]


def test_compact_todo_items_shows_all_when_small() -> None:
    visible, hidden = _compact_todo_items(_items(4))

    assert hidden == 0
    assert [item["id"] for item in visible if item] == ["1", "2", "3", "4"]
