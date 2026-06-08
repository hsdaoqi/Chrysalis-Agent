"""子 agent 并发执行、失败隔离、事件回传的单元测试。"""
from __future__ import annotations

import threading

import pytest

import chrysalis.subagent as subagent
import chrysalis.agent_loop as agent_loop_mod
import chrysalis.tools as tools_mod


class _FakeLoop:
    """替身 AgentLoop：根据 task 内容模拟成功/失败/异常，并触发回调。

    task 约定：
      - 以 "FAIL:" 开头 → loop.run 返回 ok=False
      - 以 "RAISE:" 开头 → loop.run 抛异常
      - 以 "NEEDUSER:" 开头 → 返回 need_user
      - 以 "NESTED:" 开头 → 在 run 内再次调用 run_tasks（模拟嵌套派生）
      - 其它 → 返回 ok=True，final = "done:<task>"
    """

    nested_results: dict[str, dict] = {}

    def __init__(self, *, progress=None, on_tool_call=None, **kwargs):
        self.progress = progress
        self.on_tool_call = on_tool_call

    def run(self, task: str, session_context: str = ""):
        # 模拟一次工具调用，触发回调
        if self.on_tool_call is not None:
            self.on_tool_call("read_file", {"path": "x"}, None)
            self.on_tool_call("read_file", {"path": "x"}, {"ok": True})
        if self.progress is not None:
            self.progress("working...")

        if task.startswith("RAISE:"):
            raise RuntimeError("boom")
        if task.startswith("FAIL:"):
            return {"ok": False, "final": "子任务失败原因"}
        if task.startswith("NEEDUSER:"):
            return {"ok": False, "need_user": True, "question": "需要确认吗?"}
        if task.startswith("NESTED:"):
            _FakeLoop.nested_results[task] = subagent.run_tasks([{"task": "inner"}])
            return {"ok": True, "final": "nested-parent-done"}
        return {"ok": True, "final": f"done:{task}"}


@pytest.fixture(autouse=True)
def _patch_subagent(monkeypatch):
    monkeypatch.setattr(subagent, "create_client", lambda cfg: object())
    monkeypatch.setattr(agent_loop_mod, "AgentLoop", _FakeLoop)
    monkeypatch.setattr(tools_mod, "generate_tools_schema", lambda **kw: [])
    # 重置模块状态
    _FakeLoop.nested_results = {}
    subagent.configure(session_config=object(), progress=None, max_workers=4)
    yield


def test_parallel_all_success():
    tasks = [{"task": "a"}, {"task": "b"}, {"task": "c"}]
    out = subagent.run_tasks(tasks)
    assert out["ok"] is True
    assert out["summary"] == {"total": 3, "succeeded": 3, "failed": 0}
    for i, r in enumerate(out["results"]):
        assert r["index"] == i
        assert r["ok"] is True
        assert r["result"] == f"done:{tasks[i]['task']}"
        assert r["error"] is None


def test_single_task_structured_result():
    out = subagent.run_tasks([{"task": "solo"}])
    assert out["ok"] is True
    assert out["summary"] == {"total": 1, "succeeded": 1, "failed": 0}
    assert out["results"][0] == {
        "index": 0,
        "task": "solo",
        "ok": True,
        "result": "done:solo",
        "error": None,
    }


def test_failure_isolation():
    tasks = [{"task": "ok1"}, {"task": "FAIL:bad"}, {"task": "RAISE:boom"}, {"task": "ok2"}]
    out = subagent.run_tasks(tasks)
    assert out["ok"] is True
    assert out["summary"] == {"total": 4, "succeeded": 2, "failed": 2}
    results = out["results"]
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False and results[1]["error"]
    assert results[2]["ok"] is False and "boom" in results[2]["error"]
    assert results[3]["ok"] is True
    # index 顺序与输入一致
    assert [r["index"] for r in results] == [0, 1, 2, 3]


def test_need_user_is_failure():
    out = subagent.run_tasks([{"task": "NEEDUSER:x"}, {"task": "ok"}])
    assert out["results"][0]["ok"] is False
    assert "需要确认" in out["results"][0]["error"]
    assert out["results"][1]["ok"] is True


def test_more_tasks_than_workers_all_complete():
    subagent.configure(session_config=object(), progress=None, max_workers=2)
    tasks = [{"task": f"t{i}"} for i in range(7)]
    out = subagent.run_tasks(tasks)
    assert out["summary"]["total"] == 7
    assert out["summary"]["succeeded"] == 7
    assert all(r["ok"] for r in out["results"])


def test_events_carry_sub_index():
    events: list[dict] = []
    lock = threading.Lock()

    def on_event(ev):
        with lock:
            events.append(ev)

    token = subagent.bind_run(progress=None, on_subagent_event=on_event)
    try:
        subagent.run_tasks([{"task": "a"}, {"task": "b"}])
    finally:
        subagent.unbind_run(token)

    assert events, "应当收到子任务事件"
    assert all("sub_index" in ev and "task" in ev and "kind" in ev for ev in events)
    seen_idx = {ev["sub_index"] for ev in events}
    assert seen_idx == {0, 1}
    kinds = {ev["kind"] for ev in events}
    assert "started" in kinds
    assert "done" in kinds
    assert "tool_started" in kinds
    assert "tool_completed" in kinds


def test_nested_spawn_rejected():
    out = subagent.run_tasks([{"task": "NESTED:p"}, {"task": "other"}])
    # 父任务本身成功
    assert out["results"][0]["ok"] is True
    # 内部嵌套调用被拒
    nested = _FakeLoop.nested_results.get("NESTED:p")
    assert nested is not None
    assert nested["ok"] is False
    assert "不允许再派生" in nested["error"]


def test_not_configured_returns_error(monkeypatch):
    monkeypatch.setattr(subagent, "_session_config", None)
    out = subagent.run_tasks([{"task": "a"}])
    assert out["ok"] is False
    assert "未配置" in out["error"]


def test_empty_tasks_rejected():
    out = subagent.run_tasks([])
    assert out["ok"] is False


def test_blank_task_description_rejected():
    out = subagent.run_tasks([{"task": "  "}])
    assert out["ok"] is False
