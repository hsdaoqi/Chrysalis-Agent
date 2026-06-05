from chrysalis.tui.app import _compact_todo_items, _todo_item_done


def _items(count: int) -> list[dict]:
    return [{"id": str(i), "title": f"{i} task", "status": "pending"} for i in range(1, count + 1)]


def test_compact_todo_items_keeps_head_and_tail() -> None:
    visible = _compact_todo_items(_items(7))

    assert [item["id"] for item in visible] == ["1", "2", "6", "7"]


def test_compact_todo_items_shows_all_when_small() -> None:
    visible = _compact_todo_items(_items(4))

    assert [item["id"] for item in visible] == ["1", "2", "3", "4"]


def test_todo_item_done_accepts_completed_status() -> None:
    assert _todo_item_done({"status": "completed"})
    assert _todo_item_done({"status": "done"})
    assert not _todo_item_done({"status": "satisfied"})
    assert not _todo_item_done({"status": "pending"})
