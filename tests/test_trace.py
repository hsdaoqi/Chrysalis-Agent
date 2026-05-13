import json

from chrysalis.trace import TraceRecorder


def test_trace_uses_head_tail_summary(tmp_path):
    path = tmp_path / "data" / "trace.log"
    final = "a" * 250 + "MIDDLE" + "z" * 250

    TraceRecorder(path).append("长结果任务", {"ok": True, "final": final, "transcript": []})

    record = json.loads(path.read_text(encoding="utf-8"))
    saved = record["final"]
    assert saved.startswith("a" * 200)
    assert saved.endswith("z" * 200)
    assert "MIDDLE" not in saved


def test_trace_records_tool_failures_and_skill_metadata(tmp_path):
    path = tmp_path / "data" / "trace.log"
    result = {
        "ok": True,
        "final": "完成",
        "elapsed_ms": 123,
        "skill": "skills/demo.py",
        "skill_steps": 3,
        "skill_generator": "fake",
        "transcript": [
            {"turn": 1, "tool": "file_read", "observation": {"ok": True}},
            {"turn": 2, "tool": "shell_run", "observation": {"ok": False, "error": "bad command"}},
            {"turn": 3, "skill": "demo_skill", "observation": {"ok": True}},
        ],
    }

    TraceRecorder(path).append("复杂任务", result)

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["elapsed_ms"] == 123
    assert record["tool_calls"] == 2
    assert record["skill_calls"] == 1
    assert record["wrote_skill"] is True
    assert record["skill_steps"] == 3
    assert record["skill_generator"] == "fake"
    assert record["tools"][1]["name"] == "shell_run"
    assert record["failed_tools"][0]["error"] == "bad command"
    assert record["skill_names"] == ["demo_skill"]


def test_trace_records_user_intervention_and_model_error(tmp_path):
    path = tmp_path / "data" / "trace.log"
    recorder = TraceRecorder(path)

    recorder.append("需要确认", {"ok": False, "need_user": True, "question": "继续吗？", "transcript": []})
    recorder.append("模型异常", {"ok": False, "model_error": True, "exception_type": "RuntimeError", "error": "boom", "transcript": []})

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["failure_reason"] == "需要用户输入"
    assert records[0]["user_question"] == "继续吗？"
    assert records[1]["model_error"] is True
    assert records[1]["failure_reason"] == "RuntimeError"
