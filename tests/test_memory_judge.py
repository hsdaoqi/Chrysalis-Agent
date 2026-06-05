from pathlib import Path

from chrysalis.memory import MemoryJudge, PersistDecision
from chrysalis.working import WorkingMemory


def _result(**kwargs):
    data = {"ok": True, "final": "done"}
    data.update(kwargs)
    return data


def test_memory_judge_rejects_volatile_work(tmp_path: Path) -> None:
    judge = MemoryJudge(ai_judge=lambda payload: {
        "should_persist": True,
        "target": "skill",
        "value_score": 0.95,
        "confidence": 0.95,
        "reason": "should not matter",
        "evidence": ["ai"],
        "stability": "high",
        "reuse_likelihood": "high",
        "safety_risk": "low",
    })
    decision = judge.judge(
        task="Check the latest weather today",
        result=_result(agent_turns=4, usage={"turns": 4}),
        working=WorkingMemory(),
        history_lines=["[USER]: Check the latest weather today"],
        tool_trace=[{"tool": "web_scan", "ok": True}],
    )

    assert decision.should_persist is False
    assert decision.target == "session_only"
    assert "volatile" in decision.reason


def test_memory_judge_rejects_secrets(tmp_path: Path) -> None:
    judge = MemoryJudge(ai_judge=lambda payload: {
        "should_persist": True,
        "target": "skill",
        "value_score": 0.99,
        "confidence": 0.99,
        "reason": "ignored",
        "evidence": ["ai"],
        "stability": "high",
        "reuse_likelihood": "high",
        "safety_risk": "low",
    })
    decision = judge.judge(
        task="Upload API key sk-1234567890abcdef1234567890abcdef",
        result=_result(agent_turns=3),
        working=WorkingMemory(),
        history_lines=["[USER]: Upload API key"],
        tool_trace=[{"tool": "file_write", "ok": True}],
    )

    assert decision.should_persist is False
    assert decision.safety_risk == "high"
    assert "secret" in decision.reason.lower()


def test_memory_judge_allows_reusable_workflow(tmp_path: Path) -> None:
    judge = MemoryJudge(ai_judge=lambda payload: {
        "should_persist": True,
        "target": "skill",
        "value_score": 0.9,
        "confidence": 0.88,
        "reason": "repeatable workflow with validated steps",
        "evidence": ["tool trace", "final output"],
        "stability": "high",
        "reuse_likelihood": "high",
        "safety_risk": "low",
    })
    decision = judge.judge(
        task="Build and verify the release package",
        result=_result(agent_turns=12),
        working=WorkingMemory(),
        history_lines=["[USER]: Build and verify the release package"],
        tool_trace=[
            {"tool": "file_read", "ok": True},
            {"tool": "code_run", "ok": True},
            {"tool": "file_patch", "ok": True},
        ],
    )

    assert decision.should_persist is True
    assert decision.target == "skill"
    assert decision.value_score >= 0.9


def test_memory_judge_routes_non_skill_memory(tmp_path: Path) -> None:
    judge = MemoryJudge(ai_judge=lambda payload: {
        "should_persist": True,
        "target": "fact",
        "value_score": 0.8,
        "confidence": 0.83,
        "reason": "stable project fact",
        "evidence": ["fact-like learning"],
        "stability": "high",
        "reuse_likelihood": "medium",
        "safety_risk": "low",
    })
    decision = judge.judge(
        task="The workspace root is D:/Project/Chrysalis",
        result=_result(agent_turns=2),
        working=WorkingMemory(),
        history_lines=["[USER]: Where is the workspace root?"],
        tool_trace=[{"tool": "file_read", "ok": True}],
    )

    assert decision.should_persist is True
    assert decision.target == "fact"
