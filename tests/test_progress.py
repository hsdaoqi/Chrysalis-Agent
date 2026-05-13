from chrysalis.progress import summarize_action, summarize_observation


def test_summarize_action_for_tool_and_final():
    tool = summarize_action(1, {"tool": "file_list", "args": {"path": "."}, "thought": "查看目录"})
    final = summarize_action(2, {"final": "完成"})

    assert tool == "第 1 轮：调用工具 file_list(.) | 查看目录"
    assert final == "第 2 轮：最终回答 | 完成"


def test_summarize_observation_for_entries():
    text = summarize_observation(1, "工具", {"ok": True, "entries": [{"name": "a"}]})

    assert text == "第 1 轮：工具结果 ok=True | entries=1"
