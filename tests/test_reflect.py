import json

from chrysalis.config import AgentConfig
from chrysalis.kernel import Kernel
from chrysalis.reflect import analyze_records, build_reflection_report, read_trace_records, reflect_traces


def make_config(tmp_path, **kwargs):
    values = {
        "data_dir": tmp_path / "data",
        "memory_dir": tmp_path / "memory",
        "skills_dir": tmp_path / "skills",
        "workspace_dir": tmp_path / "workspace",
        "max_turns": 24,
        "min_skill_turns": 16,
    }
    values.update(kwargs)
    return AgentConfig(**values)


def test_reflection_report_flags_failures_candidates_and_duplicates():
    records = [
        {"task": "重复任务", "ok": True, "final": "完成", "turns": 2, "tool_calls": 1, "skill_calls": 0},
        {"task": "重复任务", "ok": False, "final": "失败", "turns": 3, "tool_calls": 1, "skill_calls": 0},
        {
            "task": "长链路任务",
            "ok": True,
            "final": "完成",
            "turns": 16,
            "tool_calls": 3,
            "skill_calls": 0,
            "tools": [{"name": "file_read"}, {"name": "shell_run"}],
            "failed_tools": [{"name": "shell_run"}],
            "elapsed_ms": 120,
        },
        {"task": "需要确认", "ok": False, "need_user": True, "final": "继续吗？", "turns": 1, "tool_calls": 1},
        {"task": "坏技能", "ok": True, "skill_validation_error": "禁止导入 subprocess", "final": "完成", "turns": 16, "tool_calls": 3},
        {"task": "模型坏了", "ok": False, "model_error": True, "exception_type": "RuntimeError", "final": "异常", "turns": 0, "tool_calls": 0},
    ]

    report = build_reflection_report(records, min_skill_turns=16)

    assert "失败任务" in report
    assert "技能候选" in report
    assert "失败工具" in report
    assert "用户介入" in report
    assert "模型/循环异常" in report
    assert "技能验证失败" in report
    assert "长链路任务" in report
    assert "重复任务（2 次）" in report


def test_reflect_traces_writes_report(tmp_path):
    config = make_config(tmp_path)
    config.trace_log.write_text(
        "\n".join([
            json.dumps({"task": "失败任务", "ok": False, "final": "失败", "turns": 1, "tool_calls": 1, "skill_calls": 0}, ensure_ascii=False),
            json.dumps({"task": "长链路任务", "ok": True, "final": "完成", "turns": 16, "tool_calls": 3, "skill_calls": 0}, ensure_ascii=False),
        ]),
        encoding="utf-8",
    )

    result = reflect_traces(config)

    assert result["ok"] is True
    assert result["records"] == 2
    assert "长链路任务" in result["report"]
    assert "失败任务" in result["report"]


def test_read_trace_records_ignores_bad_lines(tmp_path):
    path = tmp_path / "trace.log"
    path.write_text('bad\n{"task":"ok"}\n', encoding="utf-8")

    records = read_trace_records(path)

    assert records == [{"task": "ok"}]


def test_kernel_reflects_recent_runs_without_llm(tmp_path):
    config = make_config(tmp_path)
    config.trace_log.write_text(
        json.dumps({"task": "旧任务", "ok": True, "final": "完成", "turns": 1, "tool_calls": 1, "skill_calls": 0}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = Kernel(config=config, llm=None).run("复盘最近运行")

    assert result["ok"] is True
    assert result["records"] == 1
    assert "旧任务" in result["report"]


def test_analyze_records_counts_new_trace_fields():
    records = [
        {
            "task": "候选",
            "ok": True,
            "turns": 16,
            "tool_calls": 3,
            "skill_calls": 0,
            "tools": [{"name": "file_read"}],
            "failed_tools": [],
            "candidate_skill": True,
            "elapsed_ms": 10,
        },
        {
            "task": "失败",
            "ok": False,
            "need_user": True,
            "model_error": True,
            "skill_validation_error": "bad skill",
            "tools": [{"name": "shell_run"}],
            "failed_tools": [{"name": "shell_run"}],
            "elapsed_ms": 30,
        },
    ]

    summary = analyze_records(records, min_skill_turns=16)

    assert summary["success"] == 1
    assert summary["avg_elapsed_ms"] == 20
    assert len(summary["skill_candidates"]) == 1
    assert len(summary["user_interventions"]) == 1
    assert len(summary["model_errors"]) == 1
    assert len(summary["skill_validation_failures"]) == 1
    assert summary["tool_counter"]["file_read"] == 1
    assert summary["failed_tool_counter"]["shell_run"] == 1
