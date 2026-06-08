"""任务断点续跑（checkpoint resume）测试。

覆盖：
- WorkingMemory to_dict/from_dict 往返无损。
- SessionStore checkpoint 落盘 / 读取 / 删除，且不污染会话列表。
- AgentLoop cancel 产出 checkpoint，resume 还原 working/tool_trace/history_info。
- Kernel 在 cancel 时落盘、续跑后删除 checkpoint。
"""

from __future__ import annotations

from pathlib import Path

from chrysalis.agent_loop import AgentLoop
from chrysalis.session_store import SessionStore
from chrysalis.working import WorkingMemory


# ── WorkingMemory 序列化 ──

def _build_working() -> WorkingMemory:
    working = WorkingMemory()
    working.update_checkpoint(key_info="用户要部署服务", related_sop="docs/deploy.md")
    working.update_todos(
        [
            {"title": "拉取代码", "status": "completed"},
            {"title": "构建镜像", "status": "in_progress"},
            {"title": "推送上线", "status": "pending"},
        ],
        goal="完成部署",
        action="set",
    )
    working.update_plan(
        goal="安全上线",
        steps=[{"title": "跑测试", "status": "completed"}, {"title": "灰度", "status": "pending"}],
        acceptance_criteria=[{"title": "无 5xx", "status": "pending"}],
        evidence=["测试全绿"],
        status="active",
        summary="分两步走",
        action="set",
    )
    working.request_long_term_update("部署流程可复用")
    working.tick_round()
    working.tick_round()
    return working


def test_working_memory_to_dict_from_dict_roundtrip() -> None:
    working = _build_working()
    data = working.to_dict()

    restored = WorkingMemory.from_dict(data)

    assert restored.to_dict() == data
    assert restored.key_info == working.key_info
    assert restored.related_sop == working.related_sop
    assert restored.long_term_update_requested == working.long_term_update_requested
    assert [t.title for t in restored.todos] == [t.title for t in working.todos]
    assert [t.status for t in restored.todos] == [t.status for t in working.todos]
    assert restored.plan_goal == working.plan_goal
    assert [s.title for s in restored.plan_steps] == [s.title for s in working.plan_steps]
    assert restored.plan_evidence == working.plan_evidence
    assert restored.rounds_since_todo == working.rounds_since_todo
    assert restored.rounds_since_plan == working.rounds_since_plan


def test_working_memory_restore_continues_id_seed() -> None:
    working = _build_working()
    restored = WorkingMemory.from_dict(working.to_dict())

    # 还原后新增 todo，不应与已有 id 撞车
    existing_ids = {item.id for item in restored.todos}
    restored.update_todos([{"title": "新步骤"}], action="append")
    new_item = next(item for item in restored.todos if item.title == "新步骤")
    assert new_item.id not in existing_ids


def test_empty_working_memory_roundtrip() -> None:
    working = WorkingMemory()
    restored = WorkingMemory.from_dict(working.to_dict())
    assert restored.to_dict() == working.to_dict()
    assert not restored.has_active_plan()
    assert restored.todos == []


# ── SessionStore checkpoint ──

def test_session_store_checkpoint_save_load_delete(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    sid = "session_abc"
    checkpoint = {"working": {"key_info": "x"}, "tool_trace": [{"tool": "read"}], "turn": 3}

    assert store.has_checkpoint(sid) is False
    assert store.load_checkpoint(sid) is None

    store.save_checkpoint(sid, checkpoint)
    assert store.has_checkpoint(sid) is True
    assert store.load_checkpoint(sid) == checkpoint

    assert store.delete_checkpoint(sid) is True
    assert store.has_checkpoint(sid) is False
    assert store.load_checkpoint(sid) is None


def test_checkpoint_file_excluded_from_session_list(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.new_session(model="test-model")
    store.save([{"role": "user", "blocks": [{"type": "text", "text": "hi"}]}])
    sid = store.current_id or ""
    store.save_checkpoint(sid, {"turn": 1})

    sessions = store.list_sessions()
    ids = [item["id"] for item in sessions]
    # checkpoint 文件不应被当成一个会话
    assert sid in ids
    assert all(not str(item["id"]).endswith(".checkpoint") for item in sessions)
    assert len([i for i in ids if i == sid]) == 1


def test_session_delete_removes_checkpoint(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.new_session(model="test-model")
    store.save([{"role": "user", "blocks": [{"type": "text", "text": "hi"}]}])
    sid = store.current_id or ""
    store.save_checkpoint(sid, {"turn": 1})

    assert store.delete(sid) is True
    assert store.has_checkpoint(sid) is False


def test_load_corrupt_checkpoint_returns_none(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    path = store._checkpoint_path("broken")
    path.write_text("{not json", encoding="utf-8")
    assert store.load_checkpoint("broken") is None


# ── AgentLoop checkpoint / resume ──

def _make_loop(tmp_path: Path) -> AgentLoop:
    return AgentLoop(llm=None, workspace=tmp_path, max_turns=5)


def test_agent_loop_build_checkpoint(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.working.update_checkpoint(key_info="记住这个", related_sop="")
    loop._tool_trace = [{"turn": 1, "tool": "write_file", "ok": True, "path": "a.txt"}]
    loop.history_info.append("[Agent] 调用工具 write_file")
    loop._current_turn = 2

    checkpoint = loop._build_checkpoint()

    assert checkpoint["turn"] == 2
    assert checkpoint["tool_trace"] == loop._tool_trace
    assert checkpoint["working"]["key_info"] == "记住这个"
    assert "[Agent] 调用工具 write_file" in checkpoint["history_info"]


def test_cancelled_result_carries_checkpoint(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.working.update_checkpoint(key_info="state", related_sop="")
    loop._current_turn = 1

    result = loop._cancelled_result()

    assert result["cancelled"] is True
    assert "checkpoint" in result
    assert result["checkpoint"]["working"]["key_info"] == "state"


def test_apply_resume_state_restores_and_summarizes(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    checkpoint = {
        "working": {"key_info": "已完成构建", "todos": [{"title": "上线", "status": "pending"}]},
        "tool_trace": [
            {"turn": 1, "tool": "write_file", "ok": True, "path": "config.yml"},
            {"turn": 2, "tool": "shell", "ok": True, "content": "build ok"},
        ],
        "history_info": ["[USER]: 部署服务", "[Agent] 调用工具 write_file"],
        "turn": 2,
    }

    summary = loop._apply_resume_state(checkpoint)

    assert loop.working.key_info == "已完成构建"
    assert [t.title for t in loop.working.todos] == ["上线"]
    assert loop._tool_trace == checkpoint["tool_trace"]
    assert loop.history_info == checkpoint["history_info"]
    # 摘要里要点名已执行的工具，避免重复有副作用的操作
    assert "write_file" in summary
    assert "config.yml" in summary
    assert "续跑" in summary


def test_apply_resume_state_shares_history_list(tmp_path: Path) -> None:
    shared_history: list[str] = []
    loop = AgentLoop(llm=None, workspace=tmp_path, max_turns=5, history=shared_history)
    loop._apply_resume_state({
        "working": {},
        "tool_trace": [],
        "history_info": ["[USER]: 旧任务"],
        "turn": 1,
    })
    # Kernel 与 loop 共享同一个 history list，必须就地替换内容而非换引用
    assert loop.history_info is shared_history
    assert shared_history == ["[USER]: 旧任务"]


# ── Kernel 落盘 / 续跑分发 ──

class _StubLoop:
    """最小化 loop：记录 run 入参，返回预置结果。"""

    def __init__(self) -> None:
        self.runs: list[dict] = []
        self.next_result: dict = {"ok": True, "final": "done"}

    def run(self, task, session_context="", images=None, ui_kind="", resume=None):
        self.runs.append({"task": task, "resume": resume, "ui_kind": ui_kind})
        return dict(self.next_result)


class _FakeSession:
    def __init__(self) -> None:
        from types import SimpleNamespace

        self.config = SimpleNamespace(name="test-model")


class _FakeLLM:
    def __init__(self) -> None:
        self.session = _FakeSession()

    def reset_task_usage(self) -> None:
        pass

    def context_usage(self) -> dict:
        return {}


class _FakeTracker:
    def end_task(self, *args, **kwargs) -> None:
        pass

    def task_usage_dict(self) -> dict:
        return {}

    def task_cost(self, model) -> float:
        return 0.0


def _make_kernel(tmp_path: Path):
    """构造一个绕过真实 LLM 的 Kernel，仅用于测 checkpoint 落盘/续跑分发。"""
    from chrysalis.kernel import Kernel

    kernel = Kernel.__new__(Kernel)
    kernel.session_store = SessionStore(tmp_path / "sessions")
    kernel.session_store.new_session(model="test")
    kernel.loop = _StubLoop()
    kernel.llm = _FakeLLM()
    kernel.tracker = _FakeTracker()
    kernel.progress = None
    kernel.pending_user_action = None
    kernel.resume_state = None
    kernel._resume_prompt_internal = False
    kernel.on_subagent_event = None
    return kernel


def test_kernel_persists_checkpoint_on_cancel(tmp_path: Path) -> None:
    kernel = _make_kernel(tmp_path)
    sid = kernel.session_store.current_id or ""
    result = {
        "ok": False,
        "cancelled": True,
        "final": "任务已中断",
        "checkpoint": {"working": {"key_info": "k"}, "tool_trace": [], "turn": 1},
    }

    kernel._persist_checkpoint(result, "部署任务")

    assert kernel.session_store.has_checkpoint(sid)
    stored = kernel.session_store.load_checkpoint(sid)
    assert stored["task"] == "部署任务"
    assert stored["turn"] == 1
    # 完整 checkpoint 已落盘，不再随结果回传
    assert "checkpoint" not in result
    assert result["resumable"] is True


def test_kernel_clears_checkpoint_on_success(tmp_path: Path) -> None:
    kernel = _make_kernel(tmp_path)
    sid = kernel.session_store.current_id or ""
    kernel.session_store.save_checkpoint(sid, {"turn": 1})

    kernel._persist_checkpoint({"ok": True, "final": "done"}, "任务")

    assert kernel.session_store.has_checkpoint(sid) is False


def test_kernel_resume_runs_with_checkpoint_then_deletes(tmp_path: Path) -> None:
    kernel = _make_kernel(tmp_path)
    sid = kernel.session_store.current_id or ""
    kernel.session_store.save_checkpoint(
        sid,
        {"task": "续跑这个任务", "working": {"key_info": "k"}, "tool_trace": [], "turn": 2},
    )

    result = kernel.resume()

    assert result["ok"] is True
    # loop.run 必须拿到 resume 状态和原任务
    assert len(kernel.loop.runs) == 1
    assert kernel.loop.runs[0]["task"] == "续跑这个任务"
    assert kernel.loop.runs[0]["resume"]["turn"] == 2
    assert kernel.loop.runs[0]["ui_kind"] == "continue_prompt"
    # 续跑成功后 checkpoint 应被清除
    assert kernel.session_store.has_checkpoint(sid) is False


def test_kernel_resume_without_checkpoint_returns_error(tmp_path: Path) -> None:
    kernel = _make_kernel(tmp_path)
    result = kernel.resume()
    assert result["ok"] is False
    assert result["error"] == "no_checkpoint"
    assert kernel.loop.runs == []
