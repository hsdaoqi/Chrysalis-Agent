"""ElectronRuntime：Electron 桌面端的 JSONL 运行时桥（mixin 组合）。"""
from __future__ import annotations

from chrysalis.electron_runtime._common import *  # noqa: F401,F403
from chrysalis.electron_runtime.tasks import TasksMixin
from chrysalis.electron_runtime.settings import SettingsMixin
from chrysalis.electron_runtime.sessions import SessionsMixin
from chrysalis.electron_runtime.workspace import WorkspaceMixin
from chrysalis.electron_runtime.gateway import GatewayMixin
from chrysalis.electron_runtime.cron import CronMixin
from chrysalis.electron_runtime.review import ReviewMixin


class ElectronRuntime(
    TasksMixin,
    SettingsMixin,
    SessionsMixin,
    WorkspaceMixin,
    GatewayMixin,
    CronMixin,
    ReviewMixin,
):
    def __init__(self) -> None:
        self._output_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._running_tasks: dict[str, _RunningTask] = {}
        self._pending_user_actions: dict[str, dict[str, Any]] = {}
        self._active_session_id = ""
        self._session_filter = ""
        self._draft_texts: dict[str, str] = {}
        self._draft_text = ""
        self._recovery_active_session_id = ""
        self._attachments: list[dict[str, Any]] = []
        self._workspace_selected_path = ""
        self._workspace_expanded: set[str] = set()
        self._workspace_changes: list[dict[str, Any]] = []
        self._workspace_preview = self._default_workspace_preview()
        self._cron_lock = threading.Lock()
        self._cron_dispatch_lock = threading.Lock()
        self._cron_daemon_stop = threading.Event()
        self._cron_daemon_thread: threading.Thread | None = None
        self._cron_daemon_interval_seconds = 60
        self._cron_daemon_started_at: str | None = None
        self._cron_daemon_last_tick_at: str | None = None
        self._cron_daemon_last_count = 0
        self._cron_daemon_last_error: str | None = None
        self._gateway_lock = threading.RLock()
        self._gateway_processes: dict[str, _GatewayProcess] = {}
        self._gateway_last_errors: dict[str, str] = {}
        self._gateway_last_logs: dict[str, Path] = {}
        self._gateway_activity_watcher_stop = threading.Event()
        self._gateway_activity_watcher_thread: threading.Thread | None = None
        self._gateway_activity_last_mtime = 0.0

        self.kernel = Kernel(progress=lambda message: self._on_progress(self._active_session_id, message))
        self._default_permission_level = _normalize_permission_level(self.kernel.config.permission_level)
        self._recovery_path = self.kernel.config.data_dir / "desktop_recovery.json"
        self._settings_path = self.kernel.config.data_dir / "desktop_settings.json"
        self._trace_archive = TraceArchive(self.kernel.config.data_dir / "desktop_traces")
        self._gateway_activity = GatewayActivityStore(self.kernel.config.data_dir / "gateway_activity.json")
        self._gateway_activity_last_mtime = self._gateway_activity_mtime()
        self._settings = self._load_settings()
        self._apply_settings_to_kernel(self.kernel)
        self._load_recovery()
        self._bind_callbacks()
        self._load_initial_session()
        self._refresh_workspace_state(emit=False)

    def serve(self) -> None:
        self._start_gateway_activity_watcher()
        self._emit_event("runtime_ready", snapshot=self._snapshot())
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                command = json.loads(line)
            except json.JSONDecodeError as exc:
                self._emit_event("runtime_error", error=f"Invalid JSON command: {exc}")
                continue
            self._handle_command(command if isinstance(command, dict) else {})

    def _handle_command(self, command: dict[str, Any]) -> None:
        kind = str(command.get("type") or "").strip()
        try:
            if kind == "snapshot":
                self._respond(command, data=self._snapshot())
            elif kind == "refresh_sessions":
                self._respond(command, data=self._snapshot())
            elif kind == "run_task":
                self._run_task(command)
            elif kind == "resume_task":
                self._resume_task(command)
            elif kind == "guide_task":
                self._guide_task(command)
            elif kind == "resolve_pending_user_action":
                self._resolve_pending_user_action(command)
            elif kind == "cancel_task":
                session_id = str(command.get("session_id") or "").strip()
                if not session_id:
                    session_id = self._active_session_id or self.kernel.session_store.current_id or ""
                cancelled = self._cancel_session_task(session_id)
                self._respond(command, data={"cancelled": cancelled})
            elif kind == "new_session":
                self.kernel.new_session()
                self._active_session_id = self.kernel.session_store.current_id or ""
                self._ensure_active_session_file()
                self._store_draft(self.kernel.session_store.current_id or "", "")
                self._respond(command, data=self._snapshot())
                self._emit_event("session_changed", snapshot=self._snapshot())
            elif kind == "load_session":
                session_id = str(command.get("session_id") or "").strip()
                if not session_id:
                    self._respond(command, ok=False, error="Missing session_id.")
                    return
                if not self._session_exists(session_id) and self._gateway_activity_for_session(session_id) is None:
                    self._respond(command, ok=False, error="Session was not found.")
                    return
                if self._session_file_exists(session_id):
                    self.kernel.load_session(session_id)
                self._active_session_id = session_id
                self._draft_text = self._draft_texts.get(session_id, "")
                snapshot = self._snapshot()
                self._respond(command, data=snapshot)
            elif kind == "delete_session":
                session_id = str(command.get("session_id") or "").strip()
                if not session_id:
                    self._respond(command, ok=False, error="Missing session_id.")
                    return
                if self._session_is_busy(session_id):
                    self._respond(command, ok=False, error="A task is running in this session.")
                    return
                active = (self.kernel.session_store.current_id or "") == session_id
                ok = self.kernel.delete_session(session_id)
                if not ok:
                    self._respond(command, ok=False, error="Session was not found.")
                    return
                self._draft_texts.pop(session_id, None)
                self._pending_user_actions.pop(session_id, None)
                self._trace_archive.delete(session_id)
                self._after_session_mutation(active)
                self._respond(command, data=self._snapshot())
                self._emit_event("session_changed", snapshot=self._snapshot())
            elif kind == "rename_session":
                session_id = str(command.get("session_id") or "").strip()
                title = str(command.get("title") or "").strip()
                if not session_id or not title:
                    self._respond(command, ok=False, error="Missing session_id or title.")
                    return
                if not self._session_exists(session_id):
                    self._respond(command, ok=False, error="Session was not found.")
                    return
                ok = self.kernel.session_store.rename(session_id, title)
                if not ok:
                    self._respond(command, ok=False, error="Rename failed.")
                    return
                self._respond(command, data=self._snapshot())
                self._emit_event("session_changed", snapshot=self._snapshot())
            elif kind == "toggle_session_pinned":
                session_id = str(command.get("session_id") or "").strip()
                if not session_id:
                    self._respond(command, ok=False, error="Missing session_id.")
                    return
                pinned = False
                for item in self.kernel.session_store.list_sessions(limit=500):
                    if str(item.get("id") or "") == session_id:
                        pinned = bool(item.get("pinned", False))
                        break
                ok = self.kernel.session_store.set_pinned(session_id, not pinned)
                if not ok:
                    self._respond(command, ok=False, error="Session was not found.")
                    return
                self._respond(command, data=self._snapshot())
                self._emit_event("session_changed", snapshot=self._snapshot())
            elif kind == "set_session_filter":
                self._session_filter = str(command.get("query") or "").strip().lower()
                self._respond(command, data={"query": self._session_filter, "snapshot": self._snapshot()})
                self._emit_event("session_changed", snapshot=self._snapshot())
            elif kind == "save_draft":
                text = str(command.get("text") or "")
                self.save_draft(text)
                self._respond(command, data={"saved": True, "draft_text": text})
            elif kind == "add_attachment":
                path_or_url = str(command.get("path") or command.get("path_or_url") or "").strip()
                ok = self.add_attachment(path_or_url)
                if not ok:
                    self._respond(command, ok=False, error="Invalid attachment.")
                    return
                self._respond(command, data=self._snapshot())
                self._emit_event("attachments_changed", snapshot=self._snapshot())
            elif kind == "remove_attachment":
                row = int(command.get("row") or 0)
                ok = self.remove_attachment(row)
                if not ok:
                    self._respond(command, ok=False, error="Attachment row was not found.")
                    return
                self._respond(command, data=self._snapshot())
                self._emit_event("attachments_changed", snapshot=self._snapshot())
            elif kind == "clear_attachments":
                self.clear_attachments()
                self._respond(command, data=self._snapshot())
                self._emit_event("attachments_changed", snapshot=self._snapshot())
            elif kind == "refresh_workspace":
                self._refresh_workspace_state()
                self._respond(command, data=self._snapshot())
                self._emit_event("workspace_changed", snapshot=self._snapshot())
            elif kind == "select_workspace_path":
                path = str(command.get("path") or "").strip()
                ok = self.select_workspace_path(path)
                if not ok:
                    self._respond(command, ok=False, error="Workspace path was not found.")
                    return
                self._respond(command, data=self._snapshot())
                self._emit_event("workspace_changed", snapshot=self._snapshot())
            elif kind == "attach_workspace_path":
                path = str(command.get("path") or "").strip()
                ok = self.attach_workspace_path(path)
                if not ok:
                    self._respond(command, ok=False, error="Workspace file was not found.")
                    return
                self._respond(command, data=self._snapshot())
                self._emit_event("attachments_changed", snapshot=self._snapshot())
            elif kind == "load_settings_text":
                self._respond(command, data={"text": self._settings_text()})
            elif kind == "save_settings_text":
                raw = str(command.get("raw") or "")
                if not self.save_settings_text(raw):
                    self._respond(command, ok=False, error="Invalid settings JSON.")
                    return
                self._respond(command, data=self._snapshot())
                self._emit_event("settings_changed", snapshot=self._snapshot())
            elif kind == "reset_settings":
                self.reset_settings()
                self._respond(command, data=self._snapshot())
                self._emit_event("settings_changed", snapshot=self._snapshot())
            elif kind == "set_permission_level":
                level = str(command.get("level") or "").strip()
                if not self.set_permission_level(level):
                    self._respond(command, ok=False, error="Invalid permission level.")
                    return
                self._respond(command, data=self._snapshot())
                self._emit_event("settings_changed", snapshot=self._snapshot())
            elif kind == "review_update":
                self._handle_review_update(command)
            elif kind == "review_approve":
                self._handle_review_approve(command)
            elif kind == "review_discard":
                self._handle_review_discard(command)
            elif kind == "gateway_start":
                self._handle_gateway_start(command)
            elif kind == "gateway_stop":
                self._handle_gateway_stop(command)
            elif kind == "gateway_logs":
                self._handle_gateway_logs(command)
            elif kind == "gateway_refresh":
                self._respond_gateway_snapshot(command)
            elif kind == "cron_create":
                self._handle_cron_create(command)
            elif kind == "cron_update":
                self._handle_cron_update(command)
            elif kind == "cron_pause":
                self._handle_cron_pause(command)
            elif kind == "cron_resume":
                self._handle_cron_resume(command)
            elif kind == "cron_remove":
                self._handle_cron_remove(command)
            elif kind == "cron_run":
                self._handle_cron_run(command)
            elif kind == "cron_tick":
                self._handle_cron_tick(command)
            elif kind == "cron_daemon_start":
                self._handle_cron_daemon_start(command)
            elif kind == "cron_daemon_stop":
                self._handle_cron_daemon_stop(command)
            elif kind == "shutdown":
                self._stop_gateway_activity_watcher()
                self._stop_cron_daemon()
                self._stop_all_gateways()
                self._respond(command, data={"shutdown": True})
                raise SystemExit(0)
            else:
                self._respond(command, ok=False, error=f"Unknown command: {kind}")
        except SystemExit:
            raise
        except Exception as exc:  # pragma: no cover - surfaced to the Electron shell.
            self._respond(
                command,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                data={"traceback": traceback.format_exc()},
            )

    def _bind_callbacks(self) -> None:
        self.kernel.loop.on_stream_chunk = lambda chunk: None
        self.kernel.loop.on_tool_call = lambda tool, args, observation: None
        self.kernel.loop.on_tool_stream = lambda tool, args, chunk: None
        self.kernel.loop.on_thinking = lambda text: None
        self.kernel.loop.on_working_change = lambda snapshot: None
        self.kernel.loop.on_trace_event = lambda payload: None
        self.kernel.llm.on_trace_event = lambda payload: None
        self.kernel.on_subagent_event = lambda event: None

    def _bind_task_callbacks(self, kernel: Kernel, session_id: str, task_id: str) -> None:
        kernel.loop.on_stream_chunk = lambda chunk: self._on_stream_chunk(session_id, task_id, chunk)
        kernel.loop.on_tool_call = (
            lambda tool, args, observation: self._on_tool_call(session_id, task_id, tool, args, observation)
        )
        kernel.loop.on_tool_stream = (
            lambda tool, args, chunk: self._on_tool_stream(session_id, task_id, tool, args, chunk)
        )
        kernel.loop.on_thinking = lambda text: self._on_thinking(session_id, task_id, text)
        kernel.loop.on_working_change = lambda snapshot: self._on_working_change(session_id, task_id, snapshot)
        kernel.loop.on_trace_event = lambda payload: self._on_trace_event(session_id, task_id, payload)
        kernel.llm.on_trace_event = lambda payload: self._on_trace_event(session_id, task_id, payload)
        kernel.on_subagent_event = lambda event: self._on_subagent_event(session_id, task_id, event)

    def _reload_kernel(self) -> None:
        current_id = self.kernel.session_store.current_id or ""
        self.kernel = Kernel(progress=lambda message: self._on_progress(self._active_session_id, message))
        self._settings_path = self.kernel.config.data_dir / "desktop_settings.json"
        self._trace_archive = TraceArchive(self.kernel.config.data_dir / "desktop_traces")
        self._gateway_activity = GatewayActivityStore(self.kernel.config.data_dir / "gateway_activity.json")
        self._gateway_activity_last_mtime = self._gateway_activity_mtime()
        self._settings = self._load_settings()
        self._apply_settings_to_kernel(self.kernel)
        self._bind_callbacks()
        if current_id and self._session_exists(current_id):
            self.kernel.load_session(current_id)
            self._active_session_id = current_id
        else:
            self._load_initial_session()
        self._recovery_path = self.kernel.config.data_dir / "desktop_recovery.json"
        self._workspace_selected_path = ""
        self._workspace_expanded.clear()
        self._workspace_changes.clear()
        self._workspace_preview = self._default_workspace_preview()
        self._refresh_workspace_state(emit=False)

    def _run_settings_reload(self) -> None:
        self._settings = self._load_settings()
        self._reload_kernel()
        self._refresh_workspace_state(emit=False)

    def _on_progress(self, session_id: str, message: str) -> None:
        task_id = self._session_task_id(session_id)
        self._emit_event("status", session_id=session_id, task_id=task_id, status=message)
        if task_id:
            self._emit_trace_node(session_id, task_id, "status", status=message)

    def _on_stream_chunk(self, session_id: str, task_id: str, chunk: str) -> None:
        if chunk:
            self._emit_event("stream", session_id=session_id, task_id=task_id, content=chunk)

    def _on_tool_call(self, session_id: str, task_id: str, tool: str, args: dict, observation: dict | None) -> None:
        event = "tool_started" if observation is None else "tool_completed"
        turn = 0
        running = self._running_tasks.get(session_id)
        if running is not None:
            if observation is None:
                running.tool_turn += 1
            turn = running.tool_turn
        payload: dict[str, Any] = {
            "session_id": session_id,
            "task_id": task_id,
            "tool": tool,
            "args": args,
            "turn": turn,
        }
        if observation is not None:
            payload["observation"] = observation
        self._emit_event(event, **payload)
        if observation is None:
            self._capture_file_before(session_id, tool, args)
            return
        diff_payload = self._make_file_diff_payload(session_id, tool, args, observation)
        if diff_payload is not None:
            path, diff_text = diff_payload
            self._emit_file_diff(session_id, task_id, path, diff_text, turn)

    def _on_tool_stream(self, session_id: str, task_id: str, tool: str, args: dict, chunk: str) -> None:
        if not chunk:
            return
        turn = 0
        running = self._running_tasks.get(session_id)
        if running is not None:
            turn = running.tool_turn
        self._emit_event(
            "tool_stream",
            session_id=session_id,
            task_id=task_id,
            tool=tool,
            content=chunk,
            turn=turn,
        )

    def _on_thinking(self, session_id: str, task_id: str, text: str) -> None:
        if text:
            self._emit_event("thinking", session_id=session_id, task_id=task_id, content=text)


    def _on_working_change(self, session_id: str, task_id: str, snapshot: dict) -> None:
        self._emit_event("working", session_id=session_id, task_id=task_id, snapshot=snapshot)

    def _on_subagent_event(self, session_id: str, task_id: str, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        self._emit_event("subagent", session_id=session_id, task_id=task_id, **event)
        body = {key: value for key, value in event.items() if key != "kind"}
        body["sub_kind"] = event.get("kind")
        self._emit_trace_node(session_id, task_id, "subagent", **body)

    def _on_trace_event(self, session_id: str, task_id: str, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        kind = str(payload.get("kind") or "event")
        body = {key: value for key, value in payload.items() if key != "kind"}
        self._emit_trace_node(session_id, task_id, kind, **body)

    def _emit_trace_node(self, session_id: str, task_id: str, kind: str, **payload: Any) -> None:
        sequence = 0
        task = self._running_tasks.get(session_id)
        if task is not None:
            task.trace_seq += 1
            sequence = task.trace_seq
        call_id = str(payload.get("call_id") or "").strip()
        node_id = f"{task_id or session_id}-{sequence}-{kind}"
        if call_id:
            node_id = f"{task_id or session_id}-{kind}-{call_id}"
        node = {
            "id": node_id,
            "sequence": sequence,
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "kind": kind,
            "session_id": session_id,
            "task_id": task_id,
            **payload,
        }
        try:
            self._trace_archive.append(session_id, node)
        except Exception:
            pass
        self._emit_event("trace", session_id=session_id, task_id=task_id, trace=node)

    def _respond(
        self,
        command: dict[str, Any],
        ok: bool = True,
        data: Any | None = None,
        error: str = "",
    ) -> None:
        self._write(
            {
                "type": "response",
                "request_id": str(command.get("request_id") or ""),
                "ok": bool(ok),
                "data": data,
                "error": error,
            }
        )

    def _emit_event(self, event: str, **payload: Any) -> None:
        self._write({"type": "event", "event": event, **payload})

    def _write(self, payload: dict[str, Any]) -> None:
        with self._output_lock:
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
            sys.stdout.write("\n")
            sys.stdout.flush()


def main() -> None:
    ElectronRuntime().serve()


if __name__ == "__main__":
    main()
