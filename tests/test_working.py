from chrysalis.working import WorkingMemory


def test_working_memory_updates_and_formats_prompt():
    working = WorkingMemory()

    result = working.update_checkpoint("已经确认 workspace 为空", "plan_sop.md")

    assert result["ok"] is True
    assert result["working_checkpoint"]["key_info"] == "已经确认 workspace 为空"
    prompt = working.to_prompt()
    assert "当前短期工作记忆" in prompt
    assert "related_sop: plan_sop.md" in prompt


def test_working_memory_marks_long_term_update():
    working = WorkingMemory()

    result = working.request_long_term_update("发现稳定路径")

    assert result["ok"] is True
    assert working.snapshot()["long_term_update_requested"] == "发现稳定路径"


def test_working_memory_reset_clears_task_state():
    working = WorkingMemory()
    working.update_checkpoint("旧任务状态", "")
    working.request_long_term_update("旧经验")

    working.reset()

    assert working.snapshot() == {}
    assert working.append_to_prompt("观察结果") == "观察结果"


def test_working_memory_truncates_large_values():
    working = WorkingMemory(max_key_info_chars=5, max_related_sop_chars=4, max_reason_chars=3)

    working.update_checkpoint("abcdef", "12345")
    working.request_long_term_update("xyz123")

    assert working.snapshot() == {
        "key_info": "abcde",
        "related_sop": "1234",
        "long_term_update_requested": "xyz",
    }
