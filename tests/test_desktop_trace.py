from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from pathlib import Path

from chrysalis.desktop_trace import TraceArchive
from chrysalis.electron_runtime import ElectronRuntime, _GatewayProcess
from chrysalis.gateway.activity import GatewayActivityStore
from chrysalis.memory import MemoryReviewStore, PersistDecision


def test_trace_archive_appends_and_limits_events(tmp_path: Path) -> None:
    archive = TraceArchive(tmp_path / "traces", max_events=3)

    for index in range(5):
        archive.append("session-a", {"id": f"event-{index}", "kind": "status", "sequence": index})

    events = archive.load("session-a")
    assert [event["id"] for event in events] == ["event-2", "event-3", "event-4"]


def test_trace_archive_replaces_duplicate_event_ids(tmp_path: Path) -> None:
    archive = TraceArchive(tmp_path / "traces", max_events=10)

    archive.append("session-a", {"id": "same", "kind": "status", "status": "old"})
    archive.append("session-a", {"id": "same", "kind": "status", "status": "new"})

    events = archive.load("session-a")
    assert len(events) == 1
    assert events[0]["status"] == "new"


def test_trace_archive_recovers_from_corrupt_file(tmp_path: Path) -> None:
    archive = TraceArchive(tmp_path / "traces", max_events=10)
    archive.append("session-a", {"id": "first", "kind": "status"})
    path = next((tmp_path / "traces").glob("*.json"))
    path.write_text("{broken", encoding="utf-8")

    assert archive.load("session-a") == []

    archive.append("session-a", {"id": "after", "kind": "task_completed"})
    assert [event["id"] for event in archive.load("session-a")] == ["after"]


def test_trace_archive_sanitizes_session_filename(tmp_path: Path) -> None:
    archive = TraceArchive(tmp_path / "traces", max_events=10)

    archive.append("../odd/session", {"id": "event", "kind": "status"})

    files = list((tmp_path / "traces").glob("*.json"))
    assert len(files) == 1
    assert files[0].parent == tmp_path / "traces"
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["session_id"] == "../odd/session"


def test_electron_runtime_trace_node_is_persisted(tmp_path: Path) -> None:
    runtime = ElectronRuntime.__new__(ElectronRuntime)
    runtime._running_tasks = {}
    runtime._trace_archive = TraceArchive(tmp_path / "traces", max_events=10)
    emitted: list[dict] = []
    runtime._emit_event = lambda event, **payload: emitted.append({"event": event, **payload})

    ElectronRuntime._emit_trace_node(
        runtime,
        "session-a",
        "task-1",
        "task_started",
        status="thinking",
    )

    events = runtime._trace_snapshot_for_session("session-a")
    assert [event["kind"] for event in events] == ["task_started"]
    assert emitted[0]["event"] == "trace"


def test_electron_runtime_gateway_statuses(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CHRYSALIS_QQ_APP_ID", raising=False)
    monkeypatch.delenv("CHRYSALIS_QQ_APP_SECRET", raising=False)
    monkeypatch.delenv("CHRYSALIS_ONEBOT_WS_URL", raising=False)
    monkeypatch.setattr("chrysalis.electron_runtime.gateway.missing_gateway_dependencies", lambda platforms: [])

    runtime = ElectronRuntime.__new__(ElectronRuntime)
    runtime._gateway_lock = threading.RLock()
    runtime._gateway_processes = {}
    runtime._gateway_last_errors = {}
    runtime._gateway_last_logs = {}
    monkeypatch.setattr(runtime, "_gateway_log_dir", lambda: tmp_path)

    assert runtime._gateway_platform_snapshot("qq")["status"] == "not_configured"

    monkeypatch.setenv("CHRYSALIS_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("CHRYSALIS_FEISHU_APP_SECRET", "secret")
    assert runtime._gateway_platform_snapshot("feishu")["status"] == "configured"

    runtime._gateway_last_errors["feishu"] = "connection refused"
    failed = runtime._gateway_platform_snapshot("feishu")
    assert failed["status"] == "failed"
    assert failed["last_error"] == "connection refused"

    log_file = tmp_path / "feishu.log"
    log_file.write_text("[Gateway] running platforms: feishu\n", encoding="utf-8")
    runtime._gateway_processes["feishu"] = _GatewayProcess(
        platform="feishu",
        launch_platform="feishu",
        process=_FakeGatewayProcess(pid=4321),
        log_file=log_file,
        started_at="2026-06-05T12:00:00",
        command="python -m chrysalis.gateway.main feishu",
    )
    running = runtime._gateway_platform_snapshot("feishu")
    assert running["status"] == "running"
    assert running["pid"] == 4321


def test_electron_runtime_approves_sop_memory_as_skill_note(tmp_path: Path) -> None:
    memory_store = MemoryReviewStore(tmp_path / "data" / "memory_reviews.json", tmp_path / "memory")
    item = memory_store.create_from_decision(
        task="Remember the release checklist.",
        result={"ok": True, "final": "Run tests, build, then package."},
        decision=PersistDecision(
            should_persist=True,
            target="sop",
            value_score=0.9,
            confidence=0.9,
            reason="Reusable release workflow",
            evidence=["Repeated release steps"],
            stability="high",
            reuse_likelihood="high",
            safety_risk="low",
        ),
        session_id="session-sop",
    )
    assert item is not None

    runtime = ElectronRuntime.__new__(ElectronRuntime)
    runtime.kernel = SimpleNamespace(config=SimpleNamespace(root=tmp_path, skills_dir=tmp_path / "skills"))

    result = runtime._approve_memory_as_skill_note(item["id"], item=item, store=memory_store)

    assert result["ok"] is True
    skill = result["item"]["artifact"]
    skill_dir = Path(skill["path"])
    assert skill["status"] == "active"
    assert skill_dir.parent == tmp_path / "skills"
    assert (skill_dir / "SKILL.md").exists()
    body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "## About" in body
    assert "## Helper Files" in body
    assert "Run tests, build, then package." in body
    l1_text = (tmp_path / "memory" / "global_mem_insight.txt").read_text(encoding="utf-8")
    assert f"{skill_dir.relative_to(tmp_path).as_posix()}/SKILL.md" in l1_text.splitlines()
    assert not (tmp_path / "memory" / "global_mem.txt").exists()


def test_electron_runtime_reads_gateway_activity(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("chrysalis.electron_runtime.gateway.missing_gateway_dependencies", lambda platforms: [])
    store = GatewayActivityStore(tmp_path / "gateway_activity.json")
    store.start_task(
        task_id="gateway-task-1",
        session_id="gateway-session-a",
        session_key="gateway:qq:u1",
        platform="qq",
        source={"chat_id": "u1"},
        task="hello from qq",
        model="test-model",
    )
    store.append_stream("gateway-task-1", "streaming")
    store.working_changed("gateway-task-1", {"key_info": "active"})
    store.trace_event("gateway-task-1", {"kind": "llm_start", "model": "test-model"})

    runtime = ElectronRuntime.__new__(ElectronRuntime)
    runtime._running_tasks = {}
    runtime._gateway_activity = store
    runtime._gateway_lock = threading.RLock()
    runtime._gateway_processes = {}
    runtime._gateway_last_errors = {}
    runtime._gateway_last_logs = {}
    runtime._trace_archive = TraceArchive(tmp_path / "traces", max_events=10)

    assert runtime._session_is_busy("gateway-session-a") is True
    assert runtime._session_task_id("gateway-session-a") == "gateway-task-1"
    assert runtime._working_snapshot_for_session("gateway-session-a") == {"key_info": "active"}
    assert runtime._trace_snapshot_for_session("gateway-session-a")[0]["kind"] == "llm_start"

    snapshot = runtime._gateway_snapshot()
    assert snapshot["activities"][0]["task_id"] == "gateway-task-1"


class _FakeGatewayProcess:
    def __init__(self, pid: int, code: int | None = None) -> None:
        self.pid = pid
        self._code = code

    def poll(self) -> int | None:
        return self._code
