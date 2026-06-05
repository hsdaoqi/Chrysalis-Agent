import json
from pathlib import Path

from chrysalis.agent_loop import AgentLoop
from chrysalis.context_engine import ContextBudget, ContextEngine, RUNTIME_CONTEXT_HEADER
from chrysalis.llm.types import Response, ToolCall, Usage
from chrysalis.memory import MemoryJudge


class FakeTodoLLM:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []
        self.system = ""
        self.tools: list[dict] = []
        self._turn = 0
        self.on_trace_event = None

    def set_system(self, system: str) -> None:
        self.system = system

    def set_tools(self, tools: list[dict]) -> None:
        self.tools = tools

    def cancel(self) -> None:
        return None

    def chat(self, messages, tools=None, cancel_event=None):
        self.calls.append(messages)
        self._turn += 1
        call_id = f"fake-{self._turn}"
        if self.on_trace_event:
            self.on_trace_event({
                "kind": "llm_start",
                "call_id": call_id,
                "model": "fake-todo",
                "model_id": "fake-todo",
                "protocol": "test",
                "context": {
                    "tokens_estimate": 64,
                    "context_window": 1024,
                    "messages": len(messages),
                },
            })
        if self._turn == 1:
            response = Response(
                content="",
                raw="",
                tool_calls=[
                    ToolCall(
                        id="todo-1",
                        name="todo_write",
                        arguments=json.dumps(
                            {
                                "goal": "Ship the TODO view",
                                "todos": [
                                    {"id": "todo-1", "title": "Wire the snapshot", "status": "completed", "note": "state_snapshot"},
                                    {"id": "todo-2", "title": "Render TODO rows", "status": "pending"},
                                ],
                                "action": "set",
                            },
                            ensure_ascii=False,
                        ),
                    )
                ],
                usage=Usage(total_tokens=10),
            )
        else:
            response = Response(content="TODO recorded and ready.", raw="TODO recorded and ready.", usage=Usage(total_tokens=10))

        if self.on_trace_event:
            self.on_trace_event({
                "kind": "llm_complete",
                "call_id": call_id,
                "model": "fake-todo",
                "model_id": "fake-todo",
                "elapsed_ms": 0,
                "usage": response.usage.to_dict(),
                "cost": 0,
                "tool_calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in response.tool_calls
                ],
                "content_preview": response.content[:120],
            })

        def _gen():
            if False:
                yield ""
            return response

        return _gen()


def test_agent_loop_emits_nested_todo_snapshot(tmp_path: Path) -> None:
    snapshots: list[dict] = []

    loop = AgentLoop(
        llm=FakeTodoLLM(),
        workspace=tmp_path,
        max_turns=4,
        use_function_calling=True,
        memory_judge=MemoryJudge(ai_judge=lambda payload: {
            "should_persist": False,
            "target": "session_only",
            "value_score": 0.0,
            "confidence": 0.0,
            "reason": "skip memory in this test",
            "evidence": [],
            "stability": "low",
            "reuse_likelihood": "low",
            "safety_risk": "low",
        }),
        on_working_change=lambda snapshot: snapshots.append(snapshot),
    )

    result = loop.run("Create TODOs for the Electron TODO view")

    assert result["ok"] is True
    assert result["final"] == "TODO recorded and ready."
    assert snapshots

    latest = snapshots[-1]
    assert latest["todo"]["goal"] == "Ship the TODO view"
    assert latest["todo"]["pending_count"] == 1
    assert latest["todo"]["active_todo_id"] == "todo-2"
    assert latest["todo"]["todos"][0]["title"] == "Render TODO rows"
    assert latest["todo"]["todos"][1]["title"] == "Wire the snapshot"


def test_agent_loop_emits_trace_timeline_events(tmp_path: Path) -> None:
    traces: list[dict] = []

    llm = FakeTodoLLM()
    loop = AgentLoop(
        llm=llm,
        workspace=tmp_path,
        max_turns=4,
        use_function_calling=True,
        memory_judge=MemoryJudge(ai_judge=lambda payload: {
            "should_persist": False,
            "target": "session_only",
            "value_score": 0.0,
            "confidence": 0.0,
            "reason": "skip memory in this test",
            "evidence": [],
            "stability": "low",
            "reuse_likelihood": "low",
            "safety_risk": "low",
        }),
        on_trace_event=lambda event: traces.append(event),
    )
    llm.on_trace_event = lambda event: traces.append(event)

    result = loop.run("Create TODOs for the Electron TODO view")

    assert result["ok"] is True
    kinds = [event["kind"] for event in traces]
    assert kinds[0] == "context_assembled"
    assert kinds.count("llm_start") == 2
    assert kinds.count("llm_complete") == 2
    assert "tool_started" in kinds
    assert "tool_completed" in kinds
    assert "working_updated" in kinds

    llm_complete = next(event for event in traces if event["kind"] == "llm_complete")
    assert llm_complete["usage"]["total_tokens"] == 10
    assert llm_complete["cost"] >= 0
    assert llm_complete["elapsed_ms"] >= 0

    context_event = traces[0]
    assert context_event["included"]
    assert context_event["history_lines"] >= 1
    assert context_event["budget"]["section_count"] == len(context_event["budget"]["sections"])

    working_event = next(event for event in traces if event["kind"] == "working_updated")
    assert working_event["snapshot"]["todo"]["goal"] == "Ship the TODO view"


def test_agent_loop_places_runtime_context_at_user_tail(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    skills_dir = tmp_path / "skills"
    memory_dir.mkdir()
    skills_dir.mkdir()
    (memory_dir / "git_sop.md").write_text("git-specific runtime instructions", encoding="utf-8")
    llm = FakeTodoLLM()
    loop = AgentLoop(
        llm=llm,
        workspace=tmp_path,
        max_turns=1,
        use_function_calling=True,
        context_engine=ContextEngine(
            project_root=tmp_path,
            memory_dir=memory_dir,
            skills_dir=skills_dir,
            budget=ContextBudget(total_chars=10_000),
        ),
        memory_judge=MemoryJudge(ai_judge=lambda payload: {
            "should_persist": False,
            "target": "session_only",
            "value_score": 0.0,
            "confidence": 0.0,
            "reason": "skip memory in this test",
            "evidence": [],
            "stability": "low",
            "reuse_likelihood": "low",
            "safety_risk": "low",
        }),
    )

    loop.run("git commit workflow")

    assert RUNTIME_CONTEXT_HEADER not in llm.system
    first_user_content = llm.calls[0][0]["content"]
    assert first_user_content.startswith("git commit workflow")
    assert RUNTIME_CONTEXT_HEADER in first_user_content
    assert "git-specific runtime instructions" in first_user_content
