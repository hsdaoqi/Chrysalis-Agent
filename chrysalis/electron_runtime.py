"""JSONL runtime bridge for the Electron desktop shell."""

from __future__ import annotations

import copy
import difflib
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from chrysalis.cron.jobs import (
    CronError,
    create_job,
    list_jobs,
    load_job,
    mark_job_run,
    mark_job_started,
    pause_job,
    remove_job,
    resume_job,
    save_job_output,
    update_job,
)
from chrysalis.cron.scheduler import run_job, tick
from chrysalis.desktop_trace import TraceArchive
from chrysalis.gateway.bootstrap import (
    dependency_install_hint,
    ensure_gateway_dirs,
    gateway_process_argv,
    gateway_process_command,
    missing_gateway_dependencies,
)
from chrysalis.gateway.activity import GatewayActivityStore
from chrysalis.kernel import Kernel, format_context_usage
from chrysalis.llm.types import Usage
from chrysalis.llm.usage import _fmt_elapsed
from chrysalis.memory import MemoryReviewStore
from chrysalis.skills.curator import SkillCurator
from chrysalis.skills.store import ACTIVE_STATUS, ARCHIVED_STATUS, DRAFT_STATUS, STALE_STATUS, SkillStore
from configs.config import PROJECT_ROOT

_FILE_MODIFY_TOOLS = {"file_write", "file_patch"}
_MAX_ATTACHMENTS = 8
_ATTACHMENT_PREVIEW_CHARS = 8_000
_WORKSPACE_PREVIEW_CHARS = 16_000
_WORKSPACE_RECENT_LIMIT = 12
_WORKSPACE_DIFF_MAX_FILE_BYTES = 1_000_000
_WORKSPACE_DIFF_MAX_TOTAL_BYTES = 24_000_000
_WORKSPACE_DIFF_MAX_FILES = 2_000
_IGNORED_WORKSPACE_DIRS = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".pytest_cache_local",
    ".ruff_cache",
    ".tmp",
    ".tox",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    ".venv",
    "venv",
}
_TEXT_EXTENSIONS = {
    ".bat",
    ".c",
    ".cfg",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".diff",
    ".env",
    ".go",
    ".h",
    ".hpp",
    ".htm",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".lua",
    ".md",
    ".patch",
    ".php",
    ".ps1",
    ".py",
    ".qml",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
_DESKTOP_GATEWAY_PLATFORMS = ("qq", "wechat", "feishu")
_GATEWAY_ACTIVITY_POLL_SECONDS = 1.0
_DESKTOP_GATEWAY_LABELS = {
    "qq": "QQ",
    "wechat": "微信",
    "feishu": "飞书",
}
_QQ_ONEBOT_ENV_KEYS = (
    "CHRYSALIS_ONEBOT_WS_URL",
    "CHRYSALIS_ONEBOT_ACCESS_TOKEN",
    "CHRYSALIS_ONEBOT_ALLOWED_USERS",
    "CHRYSALIS_ONEBOT_ALLOWED_GROUPS",
    "CHRYSALIS_ONEBOT_ALLOW_ALL",
)


@dataclass
class _RunningTask:
    session_id: str
    task_id: str
    kernel: Kernel
    thread: threading.Thread
    file_before: dict[str, str]
    workspace_before: dict[str, str]
    emitted_diffs: dict[str, str]
    tool_turn: int = 0
    trace_seq: int = 0


@dataclass
class _GatewayProcess:
    platform: str
    launch_platform: str
    process: subprocess.Popen
    log_file: Path
    started_at: str
    command: str
    last_error: str = ""
    return_code: int | None = None


def _configure_stdio() -> None:
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")
        except Exception:
            continue


_configure_stdio()


class ElectronRuntime:
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
                self._respond(command, data=self._snapshot())
                self._emit_event("session_changed", snapshot=self._snapshot())
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

    def _run_task(self, command: dict[str, Any]) -> None:
        task = str(command.get("task") or "").strip()
        attachments = self._attachments.copy()
        task_with_attachments = self._compose_task_with_attachments(task, attachments)
        if not task_with_attachments.strip():
            self._respond(command, ok=False, error="Task is empty.")
            return

        session_id = str(command.get("session_id") or "").strip()
        if session_id and not self._session_exists(session_id):
            if not self._ensure_active_session_file(session_id):
                self._respond(command, ok=False, error="Session was not found.")
                return
        if not session_id:
            session_id = self._active_session_id or self.kernel.session_store.current_id or ""
        if not session_id:
            self._respond(command, ok=False, error="No active session.")
            return
        if not self._session_exists(session_id) and not self._ensure_active_session_file(session_id):
            self._respond(command, ok=False, error="Session was not found.")
            return

        with self._state_lock:
            if session_id in self._running_tasks:
                self._respond(command, ok=False, error="A task is already running in this session.")
                return
            task_id = str(command.get("task_id") or uuid.uuid4())
            pending_user_action = copy.deepcopy(self._pending_user_actions.pop(session_id, None))
            task_kernel = Kernel(
                config=self.kernel.config,
                progress=lambda message, sid=session_id: self._on_progress(sid, message),
                session_id=session_id,
            )
            if pending_user_action:
                task_kernel.pending_user_action = pending_user_action
            self._bind_task_callbacks(task_kernel, session_id, task_id)
            self._running_tasks[session_id] = _RunningTask(
                session_id=session_id,
                task_id=task_id,
                kernel=task_kernel,
                thread=threading.Thread(target=self._task_worker, args=(session_id, task_id, task_kernel, task_with_attachments), daemon=True),
                file_before={},
                workspace_before=self._snapshot_workspace_text_files(),
                emitted_diffs={},
                tool_turn=0,
            )
            self._attachments.clear()
            self._store_draft(session_id, "")

        running = self._running_tasks[session_id]
        running.thread.start()
        self._respond(command, data={"started": True, "task_id": task_id, "session_id": session_id})
        self._emit_event("attachments_changed", snapshot=self._snapshot())

    def _resolve_pending_user_action(self, command: dict[str, Any]) -> None:
        reply = str(command.get("reply") or "").strip()
        if not reply:
            self._respond(command, ok=False, error="Reply is empty.")
            return

        session_id = str(command.get("session_id") or "").strip()
        if session_id and not self._session_exists(session_id):
            if not self._ensure_active_session_file(session_id):
                self._respond(command, ok=False, error="Session was not found.")
                return
        if not session_id:
            session_id = self._active_session_id or self.kernel.session_store.current_id or ""
        if not session_id:
            self._respond(command, ok=False, error="No active session.")
            return
        if not self._session_exists(session_id) and not self._ensure_active_session_file(session_id):
            self._respond(command, ok=False, error="Session was not found.")
            return

        with self._state_lock:
            if session_id in self._running_tasks:
                self._respond(command, ok=False, error="A task is already running in this session.")
                return
            pending_user_action = copy.deepcopy(self._pending_user_actions.pop(session_id, None))
            if not pending_user_action:
                self._respond(command, ok=False, error="No pending user action in this session.")
                return
            task_id = str(command.get("task_id") or uuid.uuid4())
            task_kernel = Kernel(
                config=self.kernel.config,
                progress=lambda message, sid=session_id: self._on_progress(sid, message),
                session_id=session_id,
            )
            task_kernel.pending_user_action = pending_user_action
            self._bind_task_callbacks(task_kernel, session_id, task_id)
            self._running_tasks[session_id] = _RunningTask(
                session_id=session_id,
                task_id=task_id,
                kernel=task_kernel,
                thread=threading.Thread(target=self._task_worker, args=(session_id, task_id, task_kernel, reply), daemon=True),
                file_before={},
                workspace_before=self._snapshot_workspace_text_files(),
                emitted_diffs={},
                tool_turn=0,
            )
            self._store_draft(session_id, "")

        running = self._running_tasks[session_id]
        running.thread.start()
        self._respond(command, data={"started": True, "task_id": task_id, "session_id": session_id})

    def _task_worker(self, session_id: str, task_id: str, kernel: Kernel, task: str) -> None:
        self._emit_event("task_started", session_id=session_id, task_id=task_id, status="thinking", snapshot=self._snapshot())
        self._emit_trace_node(
            session_id,
            task_id,
            "task_started",
            status="thinking",
            model=kernel.active_model_name,
            task_preview=task[:320],
            context=kernel.llm.context_usage(),
        )
        result: dict[str, Any]
        pending_user_action: dict[str, Any] | None = None
        try:
            result = kernel.run(task)
        except Exception as exc:  # pragma: no cover - Kernel normally catches its own errors.
            result = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "final": f"Exception: {exc}",
            }
        finally:
            if result.get("need_user"):
                pending = getattr(kernel, "pending_user_action", None)
                if isinstance(pending, dict) and pending:
                    pending_user_action = copy.deepcopy(pending)
            try:
                self._emit_workspace_snapshot_diffs(session_id, task_id)
            except Exception:
                pass
            if self.kernel.session_store.current_id == session_id:
                try:
                    self.kernel.load_session(session_id)
                except Exception:
                    pass
            self._emit_trace_node(
                session_id,
                task_id,
                "task_completed",
                ok=bool(result.get("ok")),
                need_user=bool(result.get("need_user")),
                cancelled=bool(result.get("cancelled")),
                elapsed_ms=result.get("elapsed_ms", 0),
                usage=result.get("usage") or {},
                context=result.get("context") or {},
                review_summary=result.get("review_summary") or _task_review_summary(result),
                final_preview=str(result.get("final") or result.get("question") or result.get("error") or "")[:320],
            )
            with self._state_lock:
                if pending_user_action:
                    self._pending_user_actions[session_id] = pending_user_action
                else:
                    self._pending_user_actions.pop(session_id, None)
                self._running_tasks.pop(session_id, None)

        result["review_summary"] = result.get("review_summary") or _task_review_summary(result)
        self._emit_event("task_done", session_id=session_id, task_id=task_id, result=result, snapshot=self._snapshot())

    def _bind_callbacks(self) -> None:
        self.kernel.loop.on_stream_chunk = lambda chunk: None
        self.kernel.loop.on_tool_call = lambda tool, args, observation: None
        self.kernel.loop.on_thinking = lambda text: None
        self.kernel.loop.on_working_change = lambda snapshot: None
        self.kernel.loop.on_trace_event = lambda payload: None
        self.kernel.llm.on_trace_event = lambda payload: None

    def _bind_task_callbacks(self, kernel: Kernel, session_id: str, task_id: str) -> None:
        kernel.loop.on_stream_chunk = lambda chunk: self._on_stream_chunk(session_id, task_id, chunk)
        kernel.loop.on_tool_call = (
            lambda tool, args, observation: self._on_tool_call(session_id, task_id, tool, args, observation)
        )
        kernel.loop.on_thinking = lambda text: self._on_thinking(session_id, task_id, text)
        kernel.loop.on_working_change = lambda snapshot: self._on_working_change(session_id, task_id, snapshot)
        kernel.loop.on_trace_event = lambda payload: self._on_trace_event(session_id, task_id, payload)
        kernel.llm.on_trace_event = lambda payload: self._on_trace_event(session_id, task_id, payload)

    def _load_initial_session(self) -> None:
        preferred = self._recovery_active_session_id if self._session_exists(self._recovery_active_session_id) else ""
        if preferred:
            self.kernel.load_session(preferred)
            self._active_session_id = preferred
            self._draft_text = self._draft_texts.get(preferred, "")
            return
        sessions = self.kernel.session_store.list_sessions(limit=500)
        if not sessions:
            return
        session_id = str(sessions[0].get("id") or "")
        if session_id:
            self.kernel.load_session(session_id)
            self._active_session_id = session_id
            self._draft_text = self._draft_texts.get(session_id, "")

    def _after_session_mutation(self, active_deleted: bool) -> None:
        current_id = self.kernel.session_store.current_id or ""
        if current_id and self._session_exists(current_id):
            self._active_session_id = current_id
            self._draft_text = self._draft_texts.get(current_id, "")
            return
        sessions = self.kernel.session_store.list_sessions(limit=500)
        if sessions:
            session_id = str(sessions[0].get("id") or "")
            if session_id:
                self.kernel.load_session(session_id)
                self._active_session_id = session_id
                self._draft_text = self._draft_texts.get(session_id, "")
                return
        self.kernel.new_session()
        current_id = self.kernel.session_store.current_id or ""
        self._active_session_id = current_id
        if current_id:
            self._store_draft(current_id, "")
        self._draft_text = ""
        if active_deleted:
            self._workspace_selected_path = ""

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

    def _snapshot(self) -> dict[str, Any]:
        gateway_activity = self._gateway_activity_snapshot()
        gateway_activities = [
            item
            for item in gateway_activity.get("activities", [])
            if isinstance(item, dict) and str(item.get("session_id") or "")
        ]
        gateway_by_session = {
            str(item.get("session_id") or ""): item
            for item in gateway_activities
        }

        active_id = self._active_session_id or self.kernel.session_store.current_id or ""
        if not active_id and gateway_activities:
            active_id = str(gateway_activities[0].get("session_id") or "")
        history = self._history_for_session(active_id)
        active_turns = _count_tool_turn_cards(history)
        current_id = self.kernel.session_store.current_id or ""
        sessions = self._filtered_sessions()
        session_ids = {str(item.get("id") or "") for item in sessions}
        gateway_session_items: list[dict[str, Any]] = []
        filter_terms = self._session_filter_terms()
        for activity in gateway_activities:
            session_id = str(activity.get("session_id") or "")
            if not session_id or session_id in session_ids:
                continue
            activity_history = history if session_id == active_id else self._read_session_history(session_id)
            summary = self._gateway_activity_session_summary(activity, turns=max(
                _count_tool_turn_cards(activity_history),
                _safe_int(activity.get("turn")),
            ))
            if filter_terms and not self._session_matches_terms(summary, filter_terms):
                continue
            gateway_session_items.append(summary)
            session_ids.add(session_id)
        sessions = [*gateway_session_items, *sessions]

        def session_busy(session_id: str) -> bool:
            return bool(session_id and (session_id in self._running_tasks or session_id in gateway_by_session))

        def session_task_id(session_id: str) -> str:
            task = self._running_tasks.get(session_id)
            if task is not None:
                return task.task_id
            activity = gateway_by_session.get(session_id)
            return str(activity.get("task_id") or "") if isinstance(activity, dict) else ""

        if current_id and current_id not in session_ids:
            sessions.insert(
                0,
                {
                    "id": current_id,
                    "title": "Untitled session",
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "model": self.kernel.active_model_name,
                    "turns": active_turns,
                    "pinned": False,
                    "busy": session_busy(current_id),
                    "task_id": session_task_id(current_id),
                },
            )
        for item in sessions:
            session_id = str(item.get("id") or "")
            item["busy"] = session_busy(session_id)
            item["task_id"] = session_task_id(session_id)
            if session_id == active_id:
                item["turns"] = active_turns
                item["model"] = item.get("model") or self.kernel.active_model_name
        return {
            "active_session_id": active_id,
            "busy": self._session_is_busy(active_id),
            "model": self.kernel.active_model_name,
            "workspace_root": str(self.kernel.config.workspace_dir),
            "sessions": sessions,
            "history": history,
            "trace": self._trace_snapshot_for_session(active_id),
            "working": self._working_snapshot_for_session(active_id),
            "context": self._context_usage_for_session(active_id),
            "pending_user_action": self._pending_user_action_snapshot(active_id),
            "draft_text": self._current_draft_text(),
            "attachments": copy.deepcopy(self._attachments),
            "settings": copy.deepcopy(self._settings),
            "workspace": self._workspace_snapshot(),
            "session_filter": self._session_filter,
            "cron": self._cron_snapshot(),
            "reviews": self._review_snapshot(),
            "gateway": self._gateway_snapshot(gateway_activity),
        }

    def _history_for_session(self, session_id: str) -> list[dict[str, Any]]:
        task = self._running_tasks.get(session_id)
        try:
            if task:
                session = task.kernel.llm.session
            elif session_id and session_id != (self.kernel.session_store.current_id or ""):
                return self._read_session_history(session_id)
            else:
                session = self.kernel.llm.session
            with session._lock:
                return copy.deepcopy(session.history)
        except Exception:
            return []

    def _trace_snapshot_for_session(self, session_id: str) -> list[dict[str, Any]]:
        try:
            archived = copy.deepcopy(self._trace_archive.load(session_id))
        except Exception:
            archived = []
        activity = self._gateway_activity_for_session(session_id)
        trace = activity.get("trace") if isinstance(activity, dict) else []
        if not isinstance(trace, list) or not trace:
            return archived
        by_id: dict[str, dict[str, Any]] = {}
        for index, node in enumerate([*archived, *trace]):
            if not isinstance(node, dict):
                continue
            item = copy.deepcopy(node)
            item_id = str(item.get("id") or f"{session_id}-gateway-trace-{index}")
            item["id"] = item_id
            item.setdefault("session_id", session_id)
            by_id[item_id] = item
        return sorted(by_id.values(), key=_trace_sort_key)

    def _working_snapshot_for_session(self, session_id: str) -> dict[str, Any]:
        task = self._running_tasks.get(session_id)
        if task is None:
            activity = self._gateway_activity_for_session(session_id)
            working = activity.get("working") if isinstance(activity, dict) else None
            if isinstance(working, dict):
                return copy.deepcopy(working)
            if session_id and session_id != (self.kernel.session_store.current_id or ""):
                return {}
        try:
            loop = task.kernel.loop if task else self.kernel.loop
            return loop.working.state_snapshot()
        except Exception:
            return {}

    def _context_usage_for_session(self, session_id: str) -> dict[str, Any]:
        task = self._running_tasks.get(session_id)
        if task is None and session_id and session_id != (self.kernel.session_store.current_id or ""):
            return {}
        try:
            llm = task.kernel.llm if task else self.kernel.llm
            return llm.context_usage()
        except Exception:
            return {}

    def _pending_user_action_snapshot(self, session_id: str) -> dict[str, Any] | None:
        task = self._running_tasks.get(session_id)
        if task is not None:
            return None
        pending = self._pending_user_actions.get(session_id)
        return copy.deepcopy(pending) if isinstance(pending, dict) and pending else None

    def _current_draft_text(self) -> str:
        current_id = self._active_session_id or self.kernel.session_store.current_id or ""
        if current_id:
            return self._draft_texts.get(current_id, self._draft_text)
        return self._draft_text

    def _history(self) -> list[dict[str, Any]]:
        return self._history_for_session(self._active_session_id or self.kernel.session_store.current_id or "")

    def _working_snapshot(self) -> dict[str, Any]:
        return self._working_snapshot_for_session(self._active_session_id or self.kernel.session_store.current_id or "")

    def _cron_snapshot(self) -> dict[str, Any]:
        try:
            jobs = copy.deepcopy(list_jobs(self.kernel.config, include_disabled=True))
        except Exception as exc:
            jobs = []
            with self._cron_lock:
                self._cron_daemon_last_error = f"{type(exc).__name__}: {exc}"
        with self._cron_lock:
            thread = self._cron_daemon_thread
            running = bool(thread and thread.is_alive() and not self._cron_daemon_stop.is_set())
            daemon = {
                "running": running,
                "interval_seconds": self._cron_daemon_interval_seconds,
                "started_at": self._cron_daemon_started_at,
                "last_tick_at": self._cron_daemon_last_tick_at,
                "last_count": self._cron_daemon_last_count,
                "last_error": self._cron_daemon_last_error,
            }
        return {"daemon": daemon, "jobs": jobs}

    def _memory_review_store(self) -> MemoryReviewStore:
        return MemoryReviewStore(self.kernel.config.data_dir / "memory_reviews.json", self.kernel.config.memory_dir)

    def _skill_store(self) -> SkillStore:
        return SkillStore(self.kernel.config.skills_dir, self.kernel.config.root)

    def _review_snapshot(self) -> dict[str, Any]:
        errors: list[str] = []
        memory_items: list[dict[str, Any]] = []
        skill_items: list[dict[str, Any]] = []
        try:
            memory_items = [
                _memory_review_payload(item)
                for item in self._memory_review_store().list_items()
            ]
        except Exception as exc:
            errors.append(f"memory reviews: {type(exc).__name__}: {exc}")
        try:
            skill_records = self._skill_store().list_skills(
                status=None,
                include_drafts=True,
                include_archived=True,
            )
            skill_items = [_skill_review_payload(record) for record in skill_records]
        except Exception as exc:
            errors.append(f"skill reviews: {type(exc).__name__}: {exc}")

        items = sorted([*memory_items, *skill_items], key=_review_sort_key)
        status_counts = {"pending": 0, "approved": 0, "discarded": 0}
        kind_counts = {"memory": 0, "skill": 0}
        skill_state_counts = {"active": 0, "draft": 0, "archived": 0, "stale": 0}
        for item in items:
            status = str(item.get("status") or "")
            kind = str(item.get("kind") or "")
            if status in status_counts:
                status_counts[status] += 1
            if kind in kind_counts:
                kind_counts[kind] += 1
            if kind == "skill":
                skill_status = str(item.get("skill_status") or "")
                if skill_status in skill_state_counts:
                    skill_state_counts[skill_status] += 1

        return {
            "items": items,
            "stats": {
                "total": len(items),
                **status_counts,
                "memories": kind_counts["memory"],
                "skills": kind_counts["skill"],
                "active_skills": skill_state_counts["active"],
                "draft_skills": skill_state_counts["draft"],
                "archived_skills": skill_state_counts["archived"],
                "stale_skills": skill_state_counts["stale"],
            },
            "errors": errors,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _respond_cron_snapshot(self, command: dict[str, Any]) -> None:
        snapshot = self._snapshot()
        self._respond(command, data=snapshot)
        self._emit_event("cron_changed", snapshot=snapshot)

    def _respond_review_snapshot(self, command: dict[str, Any]) -> None:
        snapshot = self._snapshot()
        self._respond(command, data=snapshot)
        self._emit_event("review_changed", snapshot=snapshot)

    def _respond_gateway_snapshot(self, command: dict[str, Any]) -> None:
        snapshot = self._snapshot()
        self._respond(command, data=snapshot)
        self._emit_event("gateway_changed", snapshot=snapshot)

    def _gateway_snapshot(self, activity_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        platforms = [self._gateway_platform_snapshot(platform) for platform in _DESKTOP_GATEWAY_PLATFORMS]
        activity_snapshot = activity_snapshot if isinstance(activity_snapshot, dict) else self._gateway_activity_snapshot()
        activities = activity_snapshot.get("activities")
        return {
            "platforms": platforms,
            "activities": copy.deepcopy(activities) if isinstance(activities, list) else [],
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _gateway_activity_snapshot(self) -> dict[str, Any]:
        store = getattr(self, "_gateway_activity", None)
        if store is None:
            return {"version": 1, "updated_at": "", "activities": []}
        try:
            snapshot = store.snapshot()
        except Exception:
            return {"version": 1, "updated_at": "", "activities": []}
        activities = snapshot.get("activities") if isinstance(snapshot, dict) else []
        if not isinstance(activities, list):
            activities = []
        return {
            "version": _safe_int(snapshot.get("version")) if isinstance(snapshot, dict) else 1,
            "updated_at": str(snapshot.get("updated_at") or "") if isinstance(snapshot, dict) else "",
            "activities": [copy.deepcopy(item) for item in activities if isinstance(item, dict)],
        }

    def _gateway_activity_for_session(self, session_id: str) -> dict[str, Any] | None:
        session_id = str(session_id or "").strip()
        if not session_id:
            return None
        for item in self._gateway_activity_snapshot().get("activities", []):
            if isinstance(item, dict) and str(item.get("session_id") or "") == session_id:
                return copy.deepcopy(item)
        return None

    def _gateway_activity_mtime(self) -> float:
        store = getattr(self, "_gateway_activity", None)
        path = getattr(store, "path", None)
        if not isinstance(path, Path):
            return 0.0
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _start_gateway_activity_watcher(self) -> None:
        thread = getattr(self, "_gateway_activity_watcher_thread", None)
        if thread is not None and thread.is_alive():
            return
        stop = getattr(self, "_gateway_activity_watcher_stop", None)
        if stop is None:
            stop = threading.Event()
            self._gateway_activity_watcher_stop = stop
        stop.clear()
        self._gateway_activity_last_mtime = self._gateway_activity_mtime()
        self._gateway_activity_watcher_thread = threading.Thread(
            target=self._gateway_activity_watcher,
            daemon=True,
        )
        self._gateway_activity_watcher_thread.start()

    def _stop_gateway_activity_watcher(self) -> None:
        stop = getattr(self, "_gateway_activity_watcher_stop", None)
        thread = getattr(self, "_gateway_activity_watcher_thread", None)
        if stop is not None:
            stop.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        if getattr(self, "_gateway_activity_watcher_thread", None) is thread:
            self._gateway_activity_watcher_thread = None

    def _gateway_activity_watcher(self) -> None:
        stop = self._gateway_activity_watcher_stop
        try:
            while not stop.wait(_GATEWAY_ACTIVITY_POLL_SECONDS):
                mtime = self._gateway_activity_mtime()
                if mtime == self._gateway_activity_last_mtime:
                    continue
                self._gateway_activity_last_mtime = mtime
                self._emit_event("gateway_changed", snapshot=self._snapshot())
        finally:
            if getattr(self, "_gateway_activity_watcher_thread", None) is threading.current_thread():
                self._gateway_activity_watcher_thread = None

    def _gateway_activity_session_summary(self, activity: dict[str, Any], *, turns: int) -> dict[str, Any]:
        session_id = str(activity.get("session_id") or "")
        platform = str(activity.get("platform") or "gateway").strip() or "gateway"
        source = activity.get("source") if isinstance(activity.get("source"), dict) else {}
        source_label = str(source.get("description") or source.get("chat_name") or source.get("user_name") or "").strip()
        preview = str(activity.get("task_preview") or "").strip().replace("\n", " ")
        title_parts = [f"{_DESKTOP_GATEWAY_LABELS.get(platform, platform)} gateway"]
        if source_label:
            title_parts.append(source_label)
        if preview:
            title_parts.append(preview[:48])
        return {
            "id": session_id,
            "title": " · ".join(title_parts),
            "updated_at": str(activity.get("updated_at") or activity.get("started_at") or datetime.now().isoformat(timespec="seconds")),
            "model": str(activity.get("model") or self.kernel.active_model_name),
            "turns": max(0, turns),
            "pinned": False,
            "busy": True,
            "task_id": str(activity.get("task_id") or ""),
        }

    def _gateway_platform_snapshot(self, platform: str) -> dict[str, Any]:
        config = self._gateway_config(platform)
        missing_dependencies: list[str] = []
        try:
            missing_dependencies = missing_gateway_dependencies([str(config.get("launch_platform") or platform)])
        except BaseException as exc:
            if not isinstance(exc, SystemExit):
                raise
            missing_dependencies = [str(exc)]

        pid: int | None = None
        running = False
        started_at: str | None = None
        command = ""
        log_file: Path | None = None
        return_code: int | None = None
        last_error = ""
        with self._gateway_lock:
            state = self._gateway_processes.get(platform)
            last_error = self._gateway_last_errors.get(platform, "")
            log_file = self._gateway_last_logs.get(platform)
            if state is not None:
                code = state.process.poll()
                running = code is None
                pid = state.process.pid if running else None
                started_at = state.started_at
                command = state.command
                log_file = state.log_file
                return_code = code
                if code is not None:
                    state.return_code = code
                    if code != 0 and not state.last_error:
                        state.last_error = self._gateway_exit_error(state, code)
                if state.last_error:
                    last_error = state.last_error
                    self._gateway_last_errors[platform] = last_error
                self._gateway_last_logs[platform] = state.log_file

        configured = bool(config.get("configured"))
        if running:
            status = "running"
        elif not configured:
            status = "not_configured"
        elif last_error:
            status = "failed"
        else:
            status = "configured"

        log_file_text = str(log_file) if log_file else ""
        return {
            "id": platform,
            "label": _DESKTOP_GATEWAY_LABELS.get(platform, platform),
            "status": status,
            "configured": configured,
            "running": running,
            "pid": pid,
            "started_at": started_at,
            "return_code": return_code,
            "last_error": last_error,
            "configuration_error": str(config.get("configuration_error") or ""),
            "config_summary": str(config.get("summary") or ""),
            "required_config": copy.deepcopy(config.get("required_config") or []),
            "launch_platform": str(config.get("launch_platform") or platform),
            "missing_dependencies": missing_dependencies,
            "install_hint": dependency_install_hint(missing_dependencies) if missing_dependencies else "",
            "command": command,
            "log_file": log_file_text,
        }

    def _gateway_config(self, platform: str) -> dict[str, Any]:
        if platform == "qq":
            app_id = os.getenv("CHRYSALIS_QQ_APP_ID", "").strip()
            app_secret = os.getenv("CHRYSALIS_QQ_APP_SECRET", "").strip()
            official_configured = bool(app_id and app_secret)
            onebot_configured = any(os.getenv(key, "").strip() for key in _QQ_ONEBOT_ENV_KEYS)
            if official_configured:
                return {
                    "configured": True,
                    "launch_platform": "qq",
                    "summary": "Official QQ Bot credentials found",
                    "required_config": ["CHRYSALIS_QQ_APP_ID", "CHRYSALIS_QQ_APP_SECRET"],
                    "configuration_error": "",
                }
            if onebot_configured:
                ws_url = os.getenv("CHRYSALIS_ONEBOT_WS_URL", "ws://127.0.0.1:3001").strip()
                return {
                    "configured": True,
                    "launch_platform": "qq_personal",
                    "summary": f"OneBot WebSocket: {ws_url}",
                    "required_config": ["CHRYSALIS_ONEBOT_WS_URL"],
                    "configuration_error": "",
                }
            missing = ["CHRYSALIS_QQ_APP_ID", "CHRYSALIS_QQ_APP_SECRET"]
            return {
                "configured": False,
                "launch_platform": "qq",
                "summary": "Set official QQ Bot credentials, or configure CHRYSALIS_ONEBOT_WS_URL for OneBot.",
                "required_config": [*missing, "or CHRYSALIS_ONEBOT_WS_URL"],
                "configuration_error": "Missing QQ configuration: set CHRYSALIS_QQ_APP_ID and CHRYSALIS_QQ_APP_SECRET, or CHRYSALIS_ONEBOT_WS_URL.",
            }

        if platform == "wechat":
            token_file = Path(
                os.getenv("CHRYSALIS_WECHAT_TOKEN_FILE", "").strip()
                or (ensure_gateway_dirs() / "wechat_personal_token.json")
            ).expanduser()
            token_ready = token_file.exists()
            return {
                "configured": True,
                "launch_platform": "wechat_personal",
                "summary": f"Token file: {token_file}" if token_ready else "First start opens WeChat QR login.",
                "required_config": ["Optional: CHRYSALIS_WECHAT_TOKEN_FILE"],
                "configuration_error": "",
            }

        if platform == "feishu":
            app_id = os.getenv("CHRYSALIS_FEISHU_APP_ID", "").strip()
            app_secret = os.getenv("CHRYSALIS_FEISHU_APP_SECRET", "").strip()
            configured = bool(app_id and app_secret)
            missing = [
                key
                for key, value in {
                    "CHRYSALIS_FEISHU_APP_ID": app_id,
                    "CHRYSALIS_FEISHU_APP_SECRET": app_secret,
                }.items()
                if not value
            ]
            return {
                "configured": configured,
                "launch_platform": "feishu",
                "summary": "Feishu app credentials found" if configured else "Set Feishu app id and secret.",
                "required_config": ["CHRYSALIS_FEISHU_APP_ID", "CHRYSALIS_FEISHU_APP_SECRET"],
                "configuration_error": f"Missing Feishu configuration: {', '.join(missing)}." if missing else "",
            }

        return {
            "configured": False,
            "launch_platform": platform,
            "summary": "",
            "required_config": [],
            "configuration_error": f"Unsupported gateway platform: {platform}",
        }

    def _handle_gateway_start(self, command: dict[str, Any]) -> None:
        platform = _normalize_desktop_gateway_platform(command.get("platform"))
        if not platform:
            self._respond(command, ok=False, error="Missing or unsupported gateway platform.")
            return

        with self._gateway_lock:
            state = self._gateway_processes.get(platform)
            if state is not None and state.process.poll() is None:
                self._respond_gateway_snapshot(command)
                return
            if state is not None and state.process.poll() is not None:
                self._gateway_last_logs[platform] = state.log_file

        config = self._gateway_config(platform)
        if not config.get("configured"):
            error = str(config.get("configuration_error") or "Gateway is not configured.")
            with self._gateway_lock:
                self._gateway_last_errors[platform] = error
            snapshot = self._snapshot()
            self._respond(command, ok=False, error=error, data=snapshot)
            self._emit_event("gateway_changed", snapshot=snapshot)
            return

        launch_platform = str(config.get("launch_platform") or platform)
        try:
            missing = missing_gateway_dependencies([launch_platform])
        except SystemExit as exc:
            missing = [str(exc)]
        if missing:
            error = dependency_install_hint(missing)
            with self._gateway_lock:
                self._gateway_last_errors[platform] = error
            snapshot = self._snapshot()
            self._respond(command, ok=False, error=error, data=snapshot)
            self._emit_event("gateway_changed", snapshot=snapshot)
            return

        shared_groups = bool(command.get("shared_groups", False))
        try:
            argv = gateway_process_argv([launch_platform], shared_groups=shared_groups)
            command_text = gateway_process_command([launch_platform], shared_groups=shared_groups)
            log_dir = self._gateway_log_dir()
            log_file = log_dir / f"{platform}_{int(time.time())}.log"
            log_handle = log_file.open("a", encoding="utf-8", buffering=1)
            try:
                popen_kwargs: dict[str, Any] = {
                    "cwd": str(PROJECT_ROOT),
                    "stdout": log_handle,
                    "stderr": subprocess.STDOUT,
                    "stdin": subprocess.DEVNULL,
                }
                if os.name == "nt":
                    popen_kwargs["creationflags"] = (
                        getattr(subprocess, "CREATE_NO_WINDOW", 0)
                        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    )
                else:
                    popen_kwargs["start_new_session"] = True
                process = subprocess.Popen(argv, **popen_kwargs)
            finally:
                try:
                    log_handle.close()
                except OSError:
                    pass
        except (OSError, SystemExit, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            with self._gateway_lock:
                self._gateway_last_errors[platform] = error
            snapshot = self._snapshot()
            self._respond(command, ok=False, error=error, data=snapshot)
            self._emit_event("gateway_changed", snapshot=snapshot)
            return

        state = _GatewayProcess(
            platform=platform,
            launch_platform=launch_platform,
            process=process,
            log_file=log_file,
            started_at=datetime.now().isoformat(timespec="seconds"),
            command=command_text,
        )
        with self._gateway_lock:
            self._gateway_processes[platform] = state
            self._gateway_last_logs[platform] = log_file
            self._gateway_last_errors.pop(platform, None)

        time.sleep(0.2)
        code = process.poll()
        if code is not None and code != 0:
            state.return_code = code
            state.last_error = self._gateway_exit_error(state, code)
            with self._gateway_lock:
                self._gateway_last_errors[platform] = state.last_error
            snapshot = self._snapshot()
            self._respond(command, ok=False, error=state.last_error, data=snapshot)
            self._emit_event("gateway_changed", snapshot=snapshot)
            return

        self._respond_gateway_snapshot(command)

    def _handle_gateway_stop(self, command: dict[str, Any]) -> None:
        platform = _normalize_desktop_gateway_platform(command.get("platform"))
        if not platform:
            self._respond(command, ok=False, error="Missing or unsupported gateway platform.")
            return
        self._stop_gateway(platform, clear_error=True)
        self._respond_gateway_snapshot(command)

    def _handle_gateway_logs(self, command: dict[str, Any]) -> None:
        platform = _normalize_desktop_gateway_platform(command.get("platform"))
        if not platform:
            self._respond(command, ok=False, error="Missing or unsupported gateway platform.")
            return
        log_file = self._gateway_log_file(platform)
        log_text = self._read_gateway_log_tail(log_file, limit_chars=32_000) if log_file else ""
        self._respond(
            command,
            data={
                "platform": platform,
                "log_file": str(log_file) if log_file else "",
                "log": log_text,
            },
        )

    def _gateway_log_dir(self) -> Path:
        path = ensure_gateway_dirs() / "desktop"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _gateway_log_file(self, platform: str) -> Path | None:
        with self._gateway_lock:
            state = self._gateway_processes.get(platform)
            if state is not None:
                return state.log_file
            remembered = self._gateway_last_logs.get(platform)
            if remembered is not None and remembered.exists():
                return remembered
        try:
            matches = sorted(
                self._gateway_log_dir().glob(f"{platform}_*.log"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return None
        return matches[0] if matches else None

    def _gateway_exit_error(self, state: _GatewayProcess, code: int | None) -> str:
        tail = self._read_gateway_log_tail(state.log_file, limit_chars=4_000)
        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        excerpt = "\n".join(lines[-8:])
        prefix = f"Gateway exited with code {code}."
        return f"{prefix}\n{excerpt}".strip() if excerpt else prefix

    def _read_gateway_log_tail(self, path: Path | None, *, limit_chars: int) -> str:
        if path is None or not path.exists():
            return ""
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - limit_chars * 4))
                data = handle.read()
            return data.decode("utf-8", errors="replace")[-limit_chars:]
        except OSError:
            return ""

    def _stop_gateway(self, platform: str, *, clear_error: bool = False) -> None:
        with self._gateway_lock:
            state = self._gateway_processes.pop(platform, None)
            if clear_error:
                self._gateway_last_errors.pop(platform, None)
            if state is not None:
                self._gateway_last_logs[platform] = state.log_file
        if state is None:
            return
        process = state.process
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=3)
            except Exception:
                pass
        except Exception as exc:
            with self._gateway_lock:
                self._gateway_last_errors[platform] = f"{type(exc).__name__}: {exc}"

    def _stop_all_gateways(self) -> None:
        with self._gateway_lock:
            platforms = list(self._gateway_processes)
        for platform in platforms:
            self._stop_gateway(platform, clear_error=True)

    def _handle_review_update(self, command: dict[str, Any]) -> None:
        item_id = str(command.get("id") or command.get("item_id") or "").strip()
        kind, raw_id = _review_id_parts(item_id)
        if not raw_id:
            self._respond(command, ok=False, error="Missing review item id.")
            return
        title = _optional_string(command.get("title"))
        content = _optional_string(command.get("content"))
        description = _optional_string(command.get("description"))
        target = _optional_string(command.get("target"))
        if kind == "memory":
            item = self._memory_review_store().update_item(raw_id, title=title, content=content, target=target)
            if item is None:
                self._respond(command, ok=False, error=f"Memory review item not found: {raw_id}")
                return
            self._respond_review_snapshot(command)
            return
        if kind == "skill":
            metadata: dict[str, Any] = {}
            if title is not None:
                metadata["title"] = title.strip()
            if description is not None:
                metadata["description"] = description.strip()
            if target is not None:
                metadata["category"] = target.strip()
            record = self._skill_store().update(raw_id, metadata=metadata or None, body=content, include_drafts=True, include_archived=True)
            if record is None:
                self._respond(command, ok=False, error=f"Skill review item not found: {raw_id}")
                return
            self._respond_review_snapshot(command)
            return
        self._respond(command, ok=False, error=f"Unknown review item id: {item_id}")

    def _handle_review_approve(self, command: dict[str, Any]) -> None:
        item_id = str(command.get("id") or command.get("item_id") or "").strip()
        kind, raw_id = _review_id_parts(item_id)
        if not raw_id:
            self._respond(command, ok=False, error="Missing review item id.")
            return
        title = _optional_string(command.get("title"))
        content = _optional_string(command.get("content"))
        description = _optional_string(command.get("description"))
        target = _optional_string(command.get("target"))
        if kind == "memory":
            memory_store = self._memory_review_store()
            item = memory_store.update_item(raw_id, title=title, content=content, target=target)
            if item is None:
                self._respond(command, ok=False, error=f"Memory review item not found: {raw_id}")
                return
            if str(item.get("target") or "").strip().lower() == "sop":
                result = self._approve_memory_as_skill_note(raw_id, item=item, store=memory_store)
            else:
                result = memory_store.approve(raw_id)
            if not result.get("ok"):
                self._respond(command, ok=False, error=str(result.get("error") or "Memory approve failed."))
                return
            self._respond_review_snapshot(command)
            return
        if kind == "skill":
            result = self._approve_skill_review(raw_id, title=title, description=description, body=content, category=target)
            if not result.get("ok"):
                self._respond(command, ok=False, error=str(result.get("error") or "Skill approve failed."))
                return
            self._respond_review_snapshot(command)
            return
        self._respond(command, ok=False, error=f"Unknown review item id: {item_id}")

    def _handle_review_discard(self, command: dict[str, Any]) -> None:
        item_id = str(command.get("id") or command.get("item_id") or "").strip()
        kind, raw_id = _review_id_parts(item_id)
        if not raw_id:
            self._respond(command, ok=False, error="Missing review item id.")
            return
        if kind == "memory":
            result = self._memory_review_store().discard(raw_id)
            if not result.get("ok"):
                self._respond(command, ok=False, error=str(result.get("error") or "Memory discard failed."))
                return
            self._respond_review_snapshot(command)
            return
        if kind == "skill":
            result = self._skill_store().delete(raw_id)
            if not result.get("ok"):
                self._respond(command, ok=False, error=str(result.get("error") or "Skill discard failed."))
                return
            self._respond_review_snapshot(command)
            return
        self._respond(command, ok=False, error=f"Unknown review item id: {item_id}")

    def _approve_skill_review(
        self,
        name: str,
        *,
        title: str | None = None,
        description: str | None = None,
        body: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        store = self._skill_store()
        metadata: dict[str, Any] = {
            "review": {
                "status": "approved",
                "approved_at": datetime.now().isoformat(timespec="seconds"),
            }
        }
        if title is not None:
            metadata["title"] = title.strip()
        if description is not None:
            metadata["description"] = description.strip()
        if category is not None:
            metadata["category"] = category.strip()
        record = store.update(name, metadata=metadata, body=body, include_drafts=True, include_archived=True)
        if record is None:
            return {"ok": False, "error": f"skill not found: {name}"}
        if record.status == ACTIVE_STATUS:
            return {"ok": True, "message": "skill already active", "skill": record.summary()}
        if record.status == ARCHIVED_STATUS:
            return store.restore(record.name)

        curator = SkillCurator(store=store, auto_promote=True)
        validation = curator.validate_skill(record)
        store.update(record.name, metadata={"validation": validation}, include_drafts=True, include_archived=True)
        record = store.find(record.name, include_drafts=True, include_archived=True) or record
        merge_target = curator.find_merge_target(record)
        try:
            if merge_target is not None:
                merged = curator.merge_into_active(merge_target, record, validation=validation)
                return {"ok": True, "message": "skill merged", "skill": merged.summary()}
            promoted = curator.promote_validated_draft(record, validation=validation)
            return {"ok": True, "message": "skill promoted", "skill": promoted.summary()}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _approve_memory_as_skill_note(
        self,
        item_id: str,
        *,
        item: dict[str, Any],
        store: MemoryReviewStore,
    ) -> dict[str, Any]:
        content = str(item.get("content") or "").strip()
        if not content:
            return {"ok": False, "error": "skill note content cannot be empty"}

        skill_store = self._skill_store()
        title = _skill_note_title(
            str(item.get("title") or ""),
            source_task=str(item.get("source_task") or ""),
            fallback=item_id,
        )
        name = _unique_skill_note_name(skill_store, title, item_id)
        description = _skill_note_description(item)
        decision = copy.deepcopy(item.get("decision")) if isinstance(item.get("decision"), dict) else {}
        evidence = _review_string_list(item.get("evidence"), limit=8)
        now = datetime.now().isoformat(timespec="seconds")
        record = skill_store.create(
            name=name,
            description=description,
            body=_render_memory_skill_note(
                title=title,
                description=description,
                content=content,
                source_task=str(item.get("source_task") or ""),
                item_id=item_id,
                reason=str(item.get("reason") or ""),
                evidence=evidence,
            ),
            category="sop",
            tags=["sop", "skill-note"],
            status=ACTIVE_STATUS,
            metadata={
                "title": title,
                "when_to_use": _skill_note_when_to_use(item),
                "key_steps": _memory_skill_steps(content),
                "memory_decision": decision,
                "provenance": {
                    "review_item_id": item_id,
                    "task": str(item.get("source_task") or ""),
                    "session_id": str(item.get("session_id") or ""),
                    "reason": str(item.get("reason") or ""),
                },
                "review": {
                    "status": "approved",
                    "approved_at": now,
                    "source": "memory_review",
                },
            },
        )
        return store.approve(item_id, write_global=False, artifact=record.summary())

    def _handle_cron_create(self, command: dict[str, Any]) -> None:
        spec = command.get("spec")
        if not isinstance(spec, dict):
            spec = {
                key: value
                for key, value in command.items()
                if key not in {"type", "request_id"}
            }
        context_from = spec.get("context_from") or []
        if isinstance(context_from, str):
            context_from = [part.strip() for part in re.split(r"[,\n]+", context_from) if part.strip()]
        try:
            create_job(
                self.kernel.config,
                schedule=spec["schedule"],
                prompt=str(spec.get("prompt") or ""),
                job_id=spec.get("id"),
                name=spec.get("name"),
                script=spec.get("script"),
                no_agent=bool(spec.get("no_agent", False)),
                context_from=context_from,
                workdir=spec.get("workdir"),
                deliver=spec.get("deliver", "local"),
                repeat_times=spec.get("repeat_times"),
                max_delay_minutes=spec.get("max_delay_minutes"),
            )
        except (CronError, KeyError, TypeError, ValueError) as exc:
            self._respond(command, ok=False, error=str(exc))
            return
        self._respond_cron_snapshot(command)

    def _handle_cron_update(self, command: dict[str, Any]) -> None:
        job_id = str(command.get("job_id") or "").strip()
        if not job_id:
            self._respond(command, ok=False, error="Missing job_id.")
            return
        spec = command.get("spec")
        if not isinstance(spec, dict):
            spec = {}
        context_from = spec.get("context_from") or []
        if isinstance(context_from, str):
            context_from = [part.strip() for part in re.split(r"[,\n]+", context_from) if part.strip()]
        try:
            update_job(
                self.kernel.config,
                job_id,
                schedule=spec["schedule"],
                prompt=str(spec.get("prompt") or ""),
                name=spec.get("name"),
                script=spec.get("script"),
                no_agent=bool(spec.get("no_agent", False)),
                context_from=context_from,
                workdir=spec.get("workdir"),
                deliver=spec.get("deliver", "local"),
                repeat_times=spec.get("repeat_times"),
                max_delay_minutes=spec.get("max_delay_minutes"),
            )
        except (CronError, KeyError, TypeError, ValueError) as exc:
            self._respond(command, ok=False, error=str(exc))
            return
        self._respond_cron_snapshot(command)

    def _handle_cron_pause(self, command: dict[str, Any]) -> None:
        job_id = str(command.get("job_id") or "").strip()
        if not job_id:
            self._respond(command, ok=False, error="Missing job_id.")
            return
        try:
            pause_job(self.kernel.config, job_id)
        except (CronError, KeyError, TypeError, ValueError) as exc:
            self._respond(command, ok=False, error=str(exc))
            return
        self._respond_cron_snapshot(command)

    def _handle_cron_resume(self, command: dict[str, Any]) -> None:
        job_id = str(command.get("job_id") or "").strip()
        if not job_id:
            self._respond(command, ok=False, error="Missing job_id.")
            return
        try:
            resume_job(self.kernel.config, job_id)
        except (CronError, KeyError, TypeError, ValueError) as exc:
            self._respond(command, ok=False, error=str(exc))
            return
        self._respond_cron_snapshot(command)

    def _handle_cron_remove(self, command: dict[str, Any]) -> None:
        job_id = str(command.get("job_id") or "").strip()
        if not job_id:
            self._respond(command, ok=False, error="Missing job_id.")
            return
        job = load_job(self.kernel.config, job_id)
        if job and job.get("state", {}).get("running"):
            self._respond(command, ok=False, error="Job is running.")
            return
        ok = remove_job(self.kernel.config, job_id)
        if not ok:
            self._respond(command, ok=False, error=f"Job not found: {job_id}")
            return
        self._respond_cron_snapshot(command)

    def _handle_cron_run(self, command: dict[str, Any]) -> None:
        job_id = str(command.get("job_id") or "").strip()
        if not job_id:
            self._respond(command, ok=False, error="Missing job_id.")
            return
        job = load_job(self.kernel.config, job_id)
        if not job:
            self._respond(command, ok=False, error=f"Job not found: {job_id}")
            return
        with self._cron_dispatch_lock:
            if not mark_job_started(self.kernel.config, job_id):
                self._respond(command, ok=False, error="Job is already running.")
                return
            threading.Thread(target=self._cron_job_worker, args=(job,), daemon=True).start()
        self._respond_cron_snapshot(command)

    def _handle_cron_tick(self, command: dict[str, Any]) -> None:
        try:
            count = self._run_cron_tick()
        except Exception as exc:
            self._record_cron_tick(0, f"{type(exc).__name__}: {exc}")
            self._respond(command, ok=False, error=str(exc))
            return
        with self._cron_lock:
            self._cron_daemon_last_count = count
        self._respond_cron_snapshot(command)

    def _handle_cron_daemon_start(self, command: dict[str, Any]) -> None:
        try:
            interval_seconds = int(command.get("interval_seconds") or 60)
        except (TypeError, ValueError):
            interval_seconds = 60
        self._start_cron_daemon(max(1, interval_seconds))
        self._respond_cron_snapshot(command)

    def _handle_cron_daemon_stop(self, command: dict[str, Any]) -> None:
        self._stop_cron_daemon()
        self._respond_cron_snapshot(command)

    def _start_cron_daemon(self, interval_seconds: int) -> None:
        thread_to_start: threading.Thread | None = None
        with self._cron_lock:
            self._cron_daemon_interval_seconds = interval_seconds
            if self._cron_daemon_thread and self._cron_daemon_thread.is_alive():
                return
            self._cron_daemon_stop.clear()
            self._cron_daemon_started_at = datetime.now().isoformat(timespec="seconds")
            self._cron_daemon_last_error = None
            self._cron_daemon_thread = threading.Thread(target=self._cron_daemon_loop, daemon=True)
            thread_to_start = self._cron_daemon_thread
        thread_to_start.start()

    def _stop_cron_daemon(self) -> None:
        with self._cron_lock:
            self._cron_daemon_stop.set()
            thread = self._cron_daemon_thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        with self._cron_lock:
            if self._cron_daemon_thread is thread and (thread is None or not thread.is_alive()):
                self._cron_daemon_thread = None

    def _cron_daemon_loop(self) -> None:
        try:
            while not self._cron_daemon_stop.is_set():
                try:
                    self._run_cron_tick()
                except Exception as exc:
                    self._record_cron_tick(0, f"{type(exc).__name__}: {exc}")
                self._emit_event("cron_changed", snapshot=self._snapshot())
                with self._cron_lock:
                    interval_seconds = self._cron_daemon_interval_seconds
                if self._cron_daemon_stop.wait(max(1, interval_seconds)):
                    break
        finally:
            with self._cron_lock:
                if self._cron_daemon_thread is threading.current_thread():
                    self._cron_daemon_thread = None

    def _run_cron_tick(self) -> int:
        with self._cron_dispatch_lock:
            count = tick(self.kernel.config, verbose=False, async_run=True)
        self._record_cron_tick(count, None)
        return count

    def _record_cron_tick(self, count: int, error: str | None) -> None:
        with self._cron_lock:
            self._cron_daemon_last_tick_at = datetime.now().isoformat(timespec="seconds")
            self._cron_daemon_last_count = count
            self._cron_daemon_last_error = error

    def _cron_job_worker(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("id") or "")
        try:
            success, output_doc, final_response, error = run_job(self.kernel.config, job)
            output_path = save_job_output(self.kernel.config, job_id, output_doc)
            mark_job_run(
                self.kernel.config,
                job_id,
                success=success and bool(final_response.strip()),
                error=error if error else None if final_response.strip() else "empty final response",
                output_path=str(output_path),
            )
        except Exception as exc:
            try:
                output_path = save_job_output(
                    self.kernel.config,
                    job_id,
                    f"# Cron Job Failed\n\nJob: {job_id}\n\nError: {type(exc).__name__}: {exc}\n",
                )
            except Exception:
                output_path = None
            mark_job_run(
                self.kernel.config,
                job_id,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                output_path=str(output_path) if output_path else None,
            )
        finally:
            self._emit_event("cron_changed", snapshot=self._snapshot())

    def _session_task_id(self, session_id: str) -> str:
        task = self._running_tasks.get(session_id)
        if task is not None:
            return task.task_id
        activity = self._gateway_activity_for_session(session_id)
        return str(activity.get("task_id") or "") if isinstance(activity, dict) else ""

    def _session_is_busy(self, session_id: str) -> bool:
        return bool(session_id and (session_id in self._running_tasks or self._gateway_activity_for_session(session_id)))

    def _cancel_session_task(self, session_id: str) -> bool:
        task = self._running_tasks.get(session_id)
        if not task:
            return False
        try:
            task.kernel.cancel()
            return True
        except Exception:
            return False

    def _running_task(self, session_id: str) -> _RunningTask | None:
        return self._running_tasks.get(session_id)

    def _store_draft(self, session_id: str, text: str) -> None:
        if session_id:
            self._draft_texts[session_id] = text
        self._draft_text = text
        data = {
            "draft_texts": self._draft_texts,
            "active_session_id": session_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            self._recovery_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def save_draft(self, text: str) -> None:
        self._store_draft(self._active_session_id or self.kernel.session_store.current_id or "", text)

    def _load_recovery(self) -> None:
        try:
            data = json.loads(self._recovery_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        drafts = data.get("draft_texts", {})
        if isinstance(drafts, dict):
            self._draft_texts = {str(key): str(value) for key, value in drafts.items()}
        legacy = data.get("draft_text")
        if isinstance(legacy, str) and not self._draft_texts:
            self._draft_text = legacy
        self._recovery_active_session_id = str(data.get("active_session_id") or "")

    def _settings_text(self) -> str:
        payload = {
            "enabled": bool(self._settings.get("enabled", False)),
            "permission_level": _normalize_permission_level(self._settings.get("permission_level")),
            "llm": copy.deepcopy(self._settings.get("llm", {})),
            "system_prompt": str(self._settings.get("system_prompt") or ""),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _load_settings(self) -> dict[str, Any]:
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._default_settings()
        return self._normalize_settings(data if isinstance(data, dict) else {})

    def _default_settings(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "permission_level": self._default_permission_level,
            "llm": {},
            "system_prompt": "",
        }

    def _normalize_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "enabled": bool(data.get("enabled", False)),
            "permission_level": _normalize_permission_level(
                data.get("permission_level") or self._default_permission_level,
            ),
            "llm": self._normalize_llm_settings(data.get("llm", {})),
            "system_prompt": str(data.get("system_prompt") or ""),
        }

    def _normalize_llm_settings(self, data: Any) -> dict[str, Any]:
        llm = data if isinstance(data, dict) else {}
        return {
            "name": str(llm.get("name") or ""),
            "provider": str(llm.get("provider") or "openai"),
            "api_key": str(llm.get("api_key") or ""),
            "base_url": str(llm.get("base_url") or ""),
            "model": str(llm.get("model") or ""),
            "wire_api": str(llm.get("wire_api") or "chat"),
            "context_window": self._to_int(llm.get("context_window"), 28000),
            "temperature": self._to_float(llm.get("temperature"), 0.2),
            "max_tokens": self._to_optional_int(llm.get("max_tokens")),
            "max_retries": self._to_int(llm.get("max_retries"), 4),
            "timeout": self._to_int(llm.get("timeout"), 60),
            "proxy": str(llm.get("proxy") or ""),
            "thinking": str(llm.get("thinking") or "disabled"),
            "thinking_budget": self._to_optional_int(llm.get("thinking_budget")),
        }

    def _to_int(self, value: Any, fallback: int) -> int:
        try:
            if value in (None, ""):
                return fallback
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def _to_optional_int(self, value: Any) -> int | None:
        try:
            if value in (None, ""):
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _to_float(self, value: Any, fallback: float) -> float:
        try:
            if value in (None, ""):
                return fallback
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def save_settings_text(self, raw: str) -> bool:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return False
        if not isinstance(data, dict):
            return False
        self._settings = self._normalize_settings(data)
        self._save_settings()
        self._reload_kernel()
        self._refresh_workspace_state(emit=False)
        return True

    def reset_settings(self) -> None:
        self._settings = self._default_settings()
        self._save_settings()
        self._reload_kernel()
        self._refresh_workspace_state(emit=False)

    def set_permission_level(self, level: str) -> bool:
        normalized = _normalize_permission_level(level, fallback="")
        if normalized not in {"locked", "balanced", "full"}:
            return False
        self._settings["permission_level"] = normalized
        self._save_settings()
        self._apply_settings_to_kernel(self.kernel)
        return True

    def _apply_settings_to_kernel(self, kernel: Kernel) -> None:
        kernel.set_permission_level(_normalize_permission_level(self._settings.get("permission_level")))

    def _save_settings(self) -> None:
        try:
            self._settings_path.write_text(json.dumps(self._settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _session_exists(self, session_id: str) -> bool:
        if not session_id:
            return False
        if self._session_file_exists(session_id):
            return True
        return any(str(item.get("id") or "") == session_id for item in self.kernel.session_store.list_sessions(limit=500))

    def _session_file_path(self, session_id: str) -> Path:
        safe_id = Path(str(session_id or "").strip()).name
        return self.kernel.config.data_dir / "sessions" / f"{safe_id}.json"

    def _session_file_exists(self, session_id: str) -> bool:
        if not session_id:
            return False
        return self._session_file_path(session_id).exists()

    def _read_session_data(self, session_id: str) -> dict[str, Any]:
        if not session_id:
            return {}
        try:
            data = json.loads(self._session_file_path(session_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _read_session_history(self, session_id: str) -> list[dict[str, Any]]:
        data = self._read_session_data(session_id)
        history = data.get("history")
        if not isinstance(history, list):
            return []
        return [copy.deepcopy(item) for item in history if isinstance(item, dict)]

    def _ensure_active_session_file(self, session_id: str | None = None) -> bool:
        session_id = str(session_id or self._active_session_id or self.kernel.session_store.current_id or "").strip()
        if not session_id:
            return False
        if self._session_exists(session_id):
            return True
        current_id = self.kernel.session_store.current_id or ""
        if current_id != session_id:
            return False
        try:
            session = self.kernel.llm.session
            with session._lock:
                history = copy.deepcopy(session.history)
            self.kernel.session_store.save(history)
        except Exception:
            return False
        return self._session_exists(session_id)

    def _filtered_sessions(self) -> list[dict[str, Any]]:
        sessions = self.kernel.session_store.list_sessions(limit=500)
        terms = self._session_filter_terms()
        if not terms:
            return sessions
        return [item for item in sessions if self._session_matches_terms(item, terms)]

    def _session_filter_terms(self) -> list[str]:
        query = self._session_filter.strip().lower()
        return [part for part in re.split(r"\s+", query) if part]

    def _session_matches_terms(self, item: dict[str, Any], terms: list[str]) -> bool:
        if not terms:
            return True
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("id", "title", "model", "updated_at")
        ).lower()
        return all(term in haystack for term in terms)

    def _workspace_snapshot(self) -> dict[str, Any]:
        return {
            "root": str(self.kernel.config.workspace_dir),
            "entries": self._workspace_entries(),
            "changes": copy.deepcopy(self._workspace_changes),
            "preview": copy.deepcopy(self._workspace_preview),
        }

    def _workspace_entries(self) -> list[dict[str, Any]]:
        root = self.kernel.config.workspace_dir
        if not root.exists():
            return []
        entries: list[dict[str, Any]] = []

        def visit(directory: Path, depth: int) -> None:
            try:
                children = sorted(
                    directory.iterdir(),
                    key=lambda item: (not item.is_dir(), item.name.lower()),
                )
            except OSError:
                children = []
            for child in children:
                if child.is_dir() and child.name in _IGNORED_WORKSPACE_DIRS:
                    continue
                child_depth = max(depth + 1, 0)
                key = _path_key(child)
                is_dir = child.is_dir()
                has_children = is_dir and self._directory_has_children(child)
                entries.append(
                    {
                        "path": str(child),
                        "name": child.name or str(child),
                        "depth": child_depth,
                        "is_dir": is_dir,
                        "has_children": has_children,
                        "expanded": bool(is_dir and key in self._workspace_expanded),
                        "selected": key == self._workspace_selected_key(),
                        "kind": _workspace_kind(child),
                        "summary": _workspace_summary(child, _workspace_kind(child)),
                    }
                )
                if is_dir and key in self._workspace_expanded:
                    visit(child, child_depth)

        visit(root, -1)
        return entries

    def _workspace_selected_key(self) -> str:
        return _path_key(self._workspace_selected_path) if self._workspace_selected_path else ""

    def _directory_has_children(self, path: Path) -> bool:
        try:
            for child in path.iterdir():
                if child.is_dir() and child.name in _IGNORED_WORKSPACE_DIRS:
                    continue
                return True
        except OSError:
            return False
        return False

    def _expand_ancestors(self, target: Path) -> None:
        root = self.kernel.config.workspace_dir.resolve()
        for parent in target.parents:
            try:
                parent.relative_to(root)
            except ValueError:
                break
            self._workspace_expanded.add(_path_key(parent))
            if _path_key(parent) == _path_key(root):
                break

    def _refresh_workspace_state(self, emit: bool = True) -> None:
        root = self.kernel.config.workspace_dir
        root.mkdir(parents=True, exist_ok=True)
        selected = self._workspace_selected_path
        if selected:
            target = self._workspace_path(selected)
            if target is None:
                selected = ""
                self._workspace_selected_path = ""
            else:
                self._workspace_selected_path = str(target)
                selected = self._workspace_selected_path
        if selected:
            self._workspace_preview = self._build_workspace_preview(Path(selected))
        else:
            self._workspace_preview = self._default_workspace_preview()
        if emit:
            self._emit_event("workspace_changed", snapshot=self._snapshot())

    def _build_workspace_preview(self, target: Path) -> dict[str, Any]:
        kind = _workspace_kind(target)
        summary = _workspace_summary(target, kind) if target.exists() else "missing"
        image_url = ""
        content = ""
        can_preview = False
        if target.is_dir():
            try:
                entries = sorted(item.name for item in target.iterdir())[:80]
            except OSError:
                entries = []
            content = "\n".join(entries) if entries else "(empty folder)"
            can_preview = True
        elif kind == "text":
            content = _workspace_preview_text(target)
            can_preview = bool(content)
        elif kind == "image":
            image_url = target.resolve().as_uri()
            content = "Image preview"
            can_preview = True
        else:
            content = "No inline preview for this file type."
        return {
            "path": str(target),
            "name": target.name or str(target),
            "kind": kind,
            "summary": summary,
            "content": content,
            "image_url": image_url,
            "can_preview": can_preview,
        }

    def _default_workspace_preview(self) -> dict[str, Any]:
        return {
            "path": "",
            "name": "",
            "kind": "",
            "summary": "Select a file to preview.",
            "content": "",
            "image_url": "",
            "can_preview": False,
        }

    def _remember_workspace_change(self, path: str) -> None:
        target = self._workspace_path(path)
        if target is None:
            return
        kind = _workspace_kind(target)
        entry = {
            "path": str(target),
            "name": target.name or str(target),
            "depth": 0,
            "is_dir": target.is_dir(),
            "has_children": False,
            "expanded": False,
            "selected": False,
            "kind": kind,
            "summary": _workspace_summary(target, kind),
        }
        for idx, existing in enumerate(self._workspace_changes):
            if existing.get("path") == entry["path"]:
                del self._workspace_changes[idx]
                break
        self._workspace_changes.insert(0, entry)
        if len(self._workspace_changes) > _WORKSPACE_RECENT_LIMIT:
            del self._workspace_changes[_WORKSPACE_RECENT_LIMIT:]
        if not self._workspace_selected_path:
            self._workspace_selected_path = str(target)
            self._workspace_preview = self._build_workspace_preview(target)
        elif _path_key(self._workspace_selected_path) == _path_key(target):
            self._workspace_preview = self._build_workspace_preview(target)
        self._refresh_workspace_state(emit=False)

    def _snapshot_workspace_text_files(self) -> dict[str, str]:
        root = self.kernel.config.workspace_dir
        try:
            root = root.resolve()
        except OSError:
            root = root.absolute()
        if not root.exists():
            return {}
        snapshot: dict[str, str] = {}
        total_bytes = 0

        def visit(directory: Path) -> None:
            nonlocal total_bytes
            if len(snapshot) >= _WORKSPACE_DIFF_MAX_FILES or total_bytes >= _WORKSPACE_DIFF_MAX_TOTAL_BYTES:
                return
            try:
                children = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
            except OSError:
                return
            for child in children:
                if len(snapshot) >= _WORKSPACE_DIFF_MAX_FILES or total_bytes >= _WORKSPACE_DIFF_MAX_TOTAL_BYTES:
                    return
                if child.is_dir():
                    if child.name in _IGNORED_WORKSPACE_DIRS:
                        continue
                    visit(child)
                    continue
                if _workspace_kind(child) != "text":
                    continue
                try:
                    size = child.stat().st_size
                except OSError:
                    continue
                if size > _WORKSPACE_DIFF_MAX_FILE_BYTES:
                    continue
                if total_bytes + size > _WORKSPACE_DIFF_MAX_TOTAL_BYTES:
                    continue
                try:
                    snapshot[str(child.resolve())] = child.read_text(encoding="utf-8", errors="replace")
                    total_bytes += size
                except OSError:
                    continue

        visit(root)
        return snapshot

    def _emit_file_diff(self, session_id: str, task_id: str, path: str, diff_text: str, turn: int = 0) -> None:
        if not diff_text.strip():
            return
        task = self._running_tasks.get(session_id)
        key = _path_key(path)
        if task is not None:
            task.emitted_diffs[key] = path
        self._remember_workspace_change(path)
        self._emit_event(
            "file_diff",
            session_id=session_id,
            task_id=task_id,
            path=path,
            diff=diff_text,
            turn=turn,
        )
        self._emit_event("workspace_changed", snapshot=self._snapshot())

    def _clear_file_diff(self, session_id: str, task_id: str, path: str, turn: int = 0) -> None:
        self._emit_event(
            "file_diff",
            session_id=session_id,
            task_id=task_id,
            path=path,
            diff="",
            turn=turn,
            clear=True,
        )

    def _emit_workspace_snapshot_diffs(self, session_id: str, task_id: str) -> None:
        task = self._running_tasks.get(session_id)
        if task is None:
            return
        before = task.workspace_before
        after = self._snapshot_workspace_text_files()
        changed_paths = sorted(set(before) | set(after))
        compared_keys = {_path_key(path) for path in changed_paths}
        final_changed_keys: set[str] = set()
        for path in changed_paths:
            before_text = before.get(path, "")
            after_text = after.get(path, "")
            if before_text == after_text:
                continue
            final_changed_keys.add(_path_key(path))
            diff = difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=f"a/{Path(path).name}",
                tofile=f"b/{Path(path).name}",
                lineterm="",
            )
            diff_text = "\n".join(line.rstrip("\n") for line in diff)
            self._emit_file_diff(session_id, task_id, path, diff_text, task.tool_turn)
        for key, path in list(task.emitted_diffs.items()):
            if key in compared_keys and key not in final_changed_keys:
                self._clear_file_diff(session_id, task_id, path, task.tool_turn)

    def _workspace_path(self, path_or_url: str) -> Path | None:
        raw = str(path_or_url or "").strip()
        if raw.startswith("Diff:"):
            raw = raw[5:].strip()
        if not raw:
            return None
        if raw.startswith("file:///"):
            raw = raw[8:]
        root = self.kernel.config.workspace_dir.resolve()
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            candidate = candidate.resolve()
        except OSError:
            candidate = candidate.absolute()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    def select_workspace_path(self, path: str) -> bool:
        target = self._workspace_path(path)
        if target is None:
            return False
        if target.is_dir():
            key = _path_key(target)
            if key in self._workspace_expanded:
                self._workspace_expanded.remove(key)
            else:
                self._workspace_expanded.add(key)
            self._refresh_workspace_state()
            return True
        self._workspace_selected_path = str(target)
        self._expand_ancestors(target)
        self._workspace_preview = self._build_workspace_preview(target)
        self._refresh_workspace_state()
        return True

    def attach_workspace_path(self, path: str) -> bool:
        target = self._workspace_path(path)
        if target is None or not target.is_file():
            return False
        return self.add_attachment(str(target))

    def add_attachment(self, path_or_url: str) -> bool:
        if len(self._attachments) >= _MAX_ATTACHMENTS:
            return False
        path = self._attachment_path(path_or_url)
        if path is None or not path.is_file():
            return False
        attachment = self._make_attachment(path)
        if any(item["path"] == attachment["path"] for item in self._attachments):
            return False
        self._attachments.append(attachment)
        return True

    def remove_attachment(self, row: int) -> bool:
        if row < 0 or row >= len(self._attachments):
            return False
        del self._attachments[row]
        return True

    def clear_attachments(self) -> None:
        self._attachments.clear()

    def _attachment_path(self, path_or_url: str) -> Path | None:
        raw = str(path_or_url or "").strip()
        if not raw:
            return None
        if raw.startswith("file:///"):
            raw = raw[8:]
        try:
            return Path(raw).expanduser().resolve()
        except OSError:
            return None

    def _make_attachment(self, path: Path) -> dict[str, Any]:
        kind = _attachment_kind(path)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return {
            "path": str(path),
            "name": path.name,
            "kind": kind,
            "summary": f"{kind} file, {_fmt_bytes(size)}",
        }

    def _compose_task_with_attachments(self, task: str, attachments: list[dict[str, Any]]) -> str:
        task = task.strip()
        if not attachments:
            return task
        if not task:
            task = "请处理我附加的文件。"
        sections = [task, "", "[ATTACHMENTS]"]
        for index, attachment in enumerate(attachments, 1):
            sections.append(f"{index}. {attachment['name']}")
            sections.append(f"   path: {attachment['path']}")
            sections.append(f"   type: {attachment['kind']}")
            sections.append(f"   summary: {attachment['summary']}")
            preview = self._attachment_preview(attachment)
            if preview:
                sections.append("   preview:")
                sections.append(self._indent(preview, "     "))
        sections.append("[/ATTACHMENTS]")
        return "\n".join(sections).strip()

    def _compose_display_task(self, task: str, attachments: list[dict[str, Any]]) -> str:
        task = task.strip()
        if not attachments:
            return task
        lines = [task or "请处理我附加的文件。", "", "Attachments:"]
        for attachment in attachments:
            lines.append(f"- {attachment['name']} ({attachment['kind']})")
        return "\n".join(lines).strip()

    def _attachment_preview(self, attachment: dict[str, Any]) -> str:
        if attachment.get("kind") != "text":
            return ""
        path = Path(str(attachment.get("path") or ""))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        if len(text) <= _ATTACHMENT_PREVIEW_CHARS:
            return text.strip()
        return text[:_ATTACHMENT_PREVIEW_CHARS].rstrip() + "\n...[preview truncated]"

    def _indent(self, text: str, prefix: str) -> str:
        return "\n".join(prefix + line for line in text.splitlines())

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

    def _on_thinking(self, session_id: str, task_id: str, text: str) -> None:
        if text:
            self._emit_event("thinking", session_id=session_id, task_id=task_id, content=text)

    def _on_working_change(self, session_id: str, task_id: str, snapshot: dict) -> None:
        self._emit_event("working", session_id=session_id, task_id=task_id, snapshot=snapshot)

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
        node = {
            "id": f"{task_id or session_id}-{sequence}-{kind}",
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

    def _capture_file_before(self, session_id: str, tool: str, args: dict) -> None:
        if tool not in _FILE_MODIFY_TOOLS:
            return
        path_str = str(args.get("path", ""))
        if not path_str:
            return
        target = self._workspace_path(path_str)
        if target is None:
            target = Path(path_str)
        try:
            task = self._running_tasks.get(session_id)
            if task is not None:
                task.file_before[path_str] = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            task = self._running_tasks.get(session_id)
            if task is not None:
                task.file_before[path_str] = ""

    def _make_file_diff_payload(self, session_id: str, tool: str, args: dict, obs: dict) -> tuple[str, str] | None:
        if tool not in _FILE_MODIFY_TOOLS or not obs.get("ok"):
            return None
        path_str = str(args.get("path", ""))
        task = self._running_tasks.get(session_id)
        before = ""
        if task is not None:
            before = task.file_before.pop(path_str, "")
        resolved = obs.get("path", path_str)
        target = self._workspace_path(str(resolved)) or Path(str(resolved))
        try:
            after = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        if before == after:
            return None
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{target.name}",
            tofile=f"b/{target.name}",
            lineterm="",
        )
        return str(target), "\n".join(line.rstrip("\n") for line in diff)

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


def _task_review_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Summarize newly-created review candidates for the desktop event stream."""

    items: list[dict[str, Any]] = []
    memory_item = result.get("memory_review_item")
    if isinstance(memory_item, dict):
        raw_id = str(memory_item.get("id") or "")
        target = str(memory_item.get("target") or "fact")
        items.append({
            "id": f"memory:{raw_id}" if raw_id else "",
            "kind": "memory",
            "title": str(memory_item.get("title") or _memory_target_label_zh(target)),
            "target": target,
            "label": _memory_target_label_zh(target),
        })

    skill = result.get("skill_draft") or result.get("skill_artifact")
    if isinstance(skill, dict):
        raw_id = str(skill.get("name") or skill.get("id") or "")
        if raw_id:
            items.append({
                "id": f"skill:{raw_id}",
                "kind": "skill",
                "title": str(skill.get("title") or raw_id),
                "target": str(skill.get("category") or ""),
                "label": "技能笔记草稿",
            })

    count = len([item for item in items if item.get("id")])
    if count <= 0:
        decision = result.get("memory_decision") if isinstance(result.get("memory_decision"), dict) else {}
        return {
            "has_candidates": False,
            "count": 0,
            "items": [],
            "headline": "",
            "reason": str(decision.get("reason") or ""),
        }
    return {
        "has_candidates": True,
        "count": count,
        "items": items,
        "headline": f"发现 {count} 个可审核的成长候选",
        "reason": "任务结束后生成了可沉淀的记忆或技能笔记草稿。",
    }


def _memory_review_summary(
    item: dict[str, Any],
    *,
    target: str,
    decision: dict[str, Any],
    evidence: list[str],
) -> dict[str, Any]:
    label = _memory_target_label_zh(target)
    reason = str(item.get("reason") or decision.get("reason") or "").strip()
    final_preview = str(item.get("final_preview") or "").strip()
    source_task = str(item.get("source_task") or "").strip()
    value_score = _optional_score(decision.get("value_score"))
    confidence = _optional_score(decision.get("confidence"))
    quality_parts = [
        f"value {value_score}" if value_score else "",
        f"confidence {confidence}" if confidence else "",
        f"stability {decision.get('stability')}" if decision.get("stability") else "",
        f"reuse {decision.get('reuse_likelihood')}" if decision.get("reuse_likelihood") else "",
    ]
    quality = " · ".join(part for part in quality_parts if part)
    if str(target or "").strip().lower() == "sop":
        title = _skill_note_title(
            str(item.get("title") or ""),
            source_task=source_task,
            fallback=str(item.get("id") or "skill-note"),
        )
        name = _skill_note_slug(title)
        return {
            "why": reason or (evidence[0] if evidence else "这条内容像可复用的操作流程，适合沉淀成技能笔记。"),
            "save_as": f"技能笔记 skills/{name}/SKILL.md；批准后不会写入 memory/global_mem.txt。",
            "reuse": _memory_reuse_text(target, source_task=source_task, final_preview=final_preview),
            "risk": _risk_text(decision.get("safety_risk"), quality=quality),
            "quality": quality,
            "next_action": "编辑适用场景和步骤后批准，或丢弃这个候选。",
        }
    return {
        "why": reason or (evidence[0] if evidence else "这条内容看起来稳定、可复用，适合进入长期记忆审核。"),
        "save_as": f"{label}，批准后会写入 memory/global_mem.txt。",
        "reuse": _memory_reuse_text(target, source_task=source_task, final_preview=final_preview),
        "risk": _risk_text(decision.get("safety_risk"), quality=quality),
        "quality": quality,
        "next_action": "编辑内容后批准，或丢弃这个候选。",
    }


def _skill_review_summary(
    record: Any,
    *,
    meta: dict[str, Any],
    review: dict[str, Any],
    provenance: dict[str, Any],
    decision: dict[str, Any],
    validation: dict[str, Any],
    evidence: list[str],
) -> dict[str, Any]:
    name = str(getattr(record, "name", "") or meta.get("name") or meta.get("id") or "skill")
    category = str(getattr(record, "category", "") or meta.get("category") or "general")
    reason = (
        str(review.get("reason") or "").strip()
        or str(provenance.get("reason") or "").strip()
        or str(decision.get("reason") or "").strip()
        or (evidence[0] if evidence else "")
    )
    tools = _review_string_list(meta.get("tools"), limit=8)
    when_to_use = _review_string_list(meta.get("when_to_use"), limit=3)
    key_steps = _review_string_list(meta.get("key_steps"), limit=4)
    validation_status = str(validation.get("status") or "unchecked")
    issues = _review_string_list(validation.get("issues"), limit=4)
    reuse = (
        "；".join(when_to_use)
        or f"后续任务命中 {category} 类别、标签或工具轨迹时，会作为相关技能笔记注入上下文。"
    )
    if key_steps:
        reuse = f"{reuse} 关键步骤：{' / '.join(key_steps)}"
    risk = (
        f"校验 {validation_status}；需处理：{'；'.join(issues)}"
        if issues
        else f"校验 {validation_status}；批准后会成为 active 技能笔记，并可在相关任务中被召回。"
    )
    return {
        "why": reason or "这次任务形成了可复用的工具路径，适合转成技能笔记草稿审核。",
        "save_as": f"技能笔记 skills/{name}/SKILL.md；类别 {category} 只作为检索标签保留。",
        "reuse": reuse,
        "risk": risk,
        "quality": f"validation {validation_status}",
        "next_action": "确认步骤和适用场景后批准，或归档这个技能笔记草稿。",
        "tools": tools,
    }


def _memory_target_label_zh(target: str) -> str:
    labels = {
        "fact": "项目事实",
        "user_profile": "用户偏好",
        "sop": "SOP/技能笔记",
    }
    return labels.get(str(target or "").strip().lower(), "长期记忆")


def _optional_score(value: Any) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{score:.2f}"


def _memory_reuse_text(target: str, *, source_task: str, final_preview: str) -> str:
    normalized = str(target or "").strip().lower()
    if normalized == "user_profile":
        return "后续任务会把它作为用户偏好注入上下文，帮助回答更贴合你的习惯。"
    if normalized == "sop":
        return "后续任务命中相关操作场景时，会作为技能笔记随上下文召回。"
    hint = source_task or final_preview
    if hint:
        return f"后续任务与这条事实相关时，会作为稳定项目记忆召回。来源线索：{hint[:180]}"
    return "后续任务与这条事实相关时，会作为稳定项目记忆召回。"


def _risk_text(safety_risk: Any, *, quality: str = "") -> str:
    risk = str(safety_risk or "").strip().lower()
    label = {
        "low": "低风险",
        "medium": "中等风险",
        "high": "高风险",
    }.get(risk, "未标记风险")
    return f"{label}{f'；{quality}' if quality else ''}"


def _skill_note_title(raw: str, *, source_task: str = "", fallback: str = "") -> str:
    text = str(raw or source_task or fallback or "skill note").strip()
    text = re.sub(
        r"^(project fact|user preference|operating note|项目事实|用户偏好|操作笔记|sop/技能笔记)\s*[:：]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+", " ", text).strip()
    return _clip_review_text(text, 120) or "skill note"


def _skill_note_slug(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    return text or "skill-note"


def _unique_skill_note_name(store: SkillStore, title: str, item_id: str) -> str:
    base = _skill_note_slug(title)[:80].strip("-_") or "skill-note"
    if store.find(base, include_drafts=True, include_archived=True) is None:
        return base
    suffix_source = _skill_note_slug(item_id).replace("mem-", "") or datetime.now().strftime("%H%M%S")
    suffix = suffix_source[-8:]
    candidate = f"{base}-{suffix}"
    if store.find(candidate, include_drafts=True, include_archived=True) is None:
        return candidate
    for index in range(2, 100):
        candidate = f"{base}-{suffix}-{index}"
        if store.find(candidate, include_drafts=True, include_archived=True) is None:
            return candidate
    return f"{base}-{suffix}-{datetime.now():%H%M%S}"


def _skill_note_description(item: dict[str, Any]) -> str:
    for value in (
        item.get("reason"),
        item.get("content"),
        item.get("source_task"),
        item.get("final_preview"),
    ):
        text = _clip_review_text(str(value or ""), 260)
        if text:
            return text
    return "Reusable SOP-style skill note captured from a reviewed memory candidate."


def _skill_note_when_to_use(item: dict[str, Any]) -> list[str]:
    values = [
        str(item.get("source_task") or "").strip(),
        str(item.get("reason") or "").strip(),
        str(item.get("title") or "").strip(),
    ]
    result: list[str] = []
    for value in values:
        text = _clip_review_text(value, 180)
        if text and text not in result:
            result.append(text)
    return result or ["Use when a task matches this reviewed SOP or recurring workflow."]


def _memory_skill_steps(content: str, *, limit: int = 6) -> list[str]:
    steps: list[str] = []
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"^\d+[.)、]\s*", "", line)
        line = _clip_review_text(line, 180)
        if line and line not in steps:
            steps.append(line)
        if len(steps) >= limit:
            break
    if steps:
        return steps
    fallback = _clip_review_text(str(content or ""), 180)
    return [fallback] if fallback else ["Follow the reviewed SOP note."]


def _render_memory_skill_note(
    *,
    title: str,
    description: str,
    content: str,
    source_task: str,
    item_id: str,
    reason: str,
    evidence: list[str],
) -> str:
    lines = [
        f"# {title}",
        "",
        "## About",
        description or "Reusable SOP-style skill note captured from a reviewed memory candidate.",
        "",
        "## When To Use",
    ]
    when = [source_task, reason]
    for item in when:
        text = _clip_review_text(item, 220)
        if text:
            lines.append(f"- {text}")
    if lines[-1] == "## When To Use":
        lines.append("- Use when a task matches this reviewed SOP or recurring workflow.")
    lines.extend(["", "## Steps"])
    for index, step in enumerate(_memory_skill_steps(content), 1):
        lines.append(f"{index}. {step}")
    lines.extend([
        "",
        "## Notes",
        content.strip(),
        "",
        "## Helper Files",
        "- Optional `.py` helper files can live next to this `SKILL.md` when the workflow needs deterministic code.",
        "",
        "## Provenance",
        f"- review_item: {item_id}",
    ])
    if source_task:
        lines.append(f"- source_task: {_clip_review_text(source_task, 300)}")
    if reason:
        lines.append(f"- reason: {_clip_review_text(reason, 300)}")
    for item in evidence[:5]:
        lines.append(f"- evidence: {_clip_review_text(item, 240)}")
    return "\n".join(lines).strip() + "\n"


def _clip_review_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _memory_review_payload(item: dict[str, Any]) -> dict[str, Any]:
    raw_id = str(item.get("id") or "")
    target = str(item.get("target") or "fact")
    content = str(item.get("content") or "")
    decision = copy.deepcopy(item.get("decision")) if isinstance(item.get("decision"), dict) else {}
    evidence = _review_string_list(item.get("evidence"), limit=12)
    summary = _memory_review_summary(item, target=target, decision=decision, evidence=evidence)
    artifact = copy.deepcopy(item.get("artifact")) if isinstance(item.get("artifact"), dict) else {}
    artifact_path = str(artifact.get("path") or "") if artifact else ""
    if artifact:
        summary = dict(summary)
        skill_name = str(artifact.get("name") or artifact.get("id") or "")
        if artifact_path:
            summary["save_as"] = f"技能笔记 {Path(artifact_path) / 'SKILL.md'}"
        elif skill_name:
            summary["save_as"] = f"技能笔记 skills/{skill_name}/SKILL.md"
    return {
        "id": f"memory:{raw_id}",
        "raw_id": raw_id,
        "kind": "memory",
        "status": _review_status(str(item.get("status") or "pending")),
        "target": target,
        "title": str(item.get("title") or _memory_target_label(target)),
        "description": str(item.get("final_preview") or item.get("source_task") or ""),
        "content": content,
        "body": content,
        "reason": str(item.get("reason") or ""),
        "evidence": evidence,
        "decision": decision,
        "review_summary": summary,
        "why": summary.get("why", ""),
        "save_as": summary.get("save_as", ""),
        "reuse": summary.get("reuse", ""),
        "risk": summary.get("risk", ""),
        "source_task": str(item.get("source_task") or ""),
        "final_preview": str(item.get("final_preview") or ""),
        "session_id": str(item.get("session_id") or ""),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "approved_at": item.get("approved_at"),
        "discarded_at": item.get("discarded_at"),
        "path": artifact_path,
        "artifact": artifact,
        "stats": {},
        "validation": {},
    }


def _skill_review_payload(record: Any) -> dict[str, Any]:
    meta = record.metadata if isinstance(getattr(record, "metadata", None), dict) else {}
    review = meta.get("review") if isinstance(meta.get("review"), dict) else {}
    provenance = meta.get("provenance") if isinstance(meta.get("provenance"), dict) else {}
    memory_decision = meta.get("memory_decision") if isinstance(meta.get("memory_decision"), dict) else {}
    validation = meta.get("validation") if isinstance(meta.get("validation"), dict) else {}
    status = _skill_review_status(str(getattr(record, "status", "") or meta.get("status") or ""), review)
    reason = (
        str(review.get("reason") or "").strip()
        or str(provenance.get("reason") or "").strip()
        or str(memory_decision.get("reason") or "").strip()
    )
    evidence = _review_string_list(review.get("evidence"), limit=6)
    if not evidence and validation.get("issues"):
        evidence = [f"validation: {issue}" for issue in _review_string_list(validation.get("issues"), limit=6)]
    if not evidence and provenance.get("history_tail"):
        evidence = _review_string_list(provenance.get("history_tail"), limit=6)
    stats = meta.get("stats") if isinstance(meta.get("stats"), dict) else {}
    summary = _skill_review_summary(record, meta=meta, review=review, provenance=provenance, decision=memory_decision, validation=validation, evidence=evidence)
    return {
        "id": f"skill:{record.name}",
        "raw_id": record.name,
        "kind": "skill",
        "status": status,
        "target": str(getattr(record, "category", "") or meta.get("category") or "general"),
        "title": str(meta.get("title") or record.name),
        "description": str(meta.get("description") or ""),
        "content": str(getattr(record, "body", "") or ""),
        "body": str(getattr(record, "body", "") or ""),
        "reason": reason,
        "evidence": evidence,
        "decision": copy.deepcopy(memory_decision),
        "review_summary": summary,
        "why": summary.get("why", ""),
        "save_as": summary.get("save_as", ""),
        "reuse": summary.get("reuse", ""),
        "risk": summary.get("risk", ""),
        "source_task": str(provenance.get("task") or ""),
        "final_preview": "",
        "session_id": str(provenance.get("session_id") or ""),
        "created_at": str(meta.get("created_at") or review.get("created_at") or ""),
        "updated_at": str(meta.get("updated_at") or ""),
        "approved_at": review.get("approved_at") or meta.get("promoted_at"),
        "discarded_at": review.get("discarded_at"),
        "path": str(getattr(record, "path", "") or ""),
        "stats": copy.deepcopy(stats),
        "validation": copy.deepcopy(validation),
        "skill_status": str(getattr(record, "status", "") or meta.get("status") or ""),
        "category": str(getattr(record, "category", "") or meta.get("category") or "general"),
        "tags": _review_string_list(meta.get("tags"), limit=12),
        "tools": _review_string_list(meta.get("tools"), limit=12),
    }


def _review_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    status_rank = {"pending": 0, "approved": 1, "discarded": 2}.get(str(item.get("status") or ""), 3)
    timestamp = str(item.get("updated_at") or item.get("created_at") or "")
    return (status_rank, _reverse_timestamp(timestamp), str(item.get("id") or ""))


def _reverse_timestamp(value: str) -> str:
    # ISO timestamps sort lexically ascending; invert printable codepoints for descending order.
    return "".join(chr(255 - min(255, ord(ch))) for ch in value)


def _safe_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _trace_sort_key(node: dict[str, Any]) -> tuple[str, int, str]:
    timestamp = str(node.get("timestamp") or "")
    sequence = _safe_int(node.get("sequence"))
    return (timestamp, sequence, str(node.get("id") or ""))


def _skill_review_status(status: str, review: dict[str, Any]) -> str:
    review_status = _review_status(str(review.get("status") or ""))
    normalized = status.strip().lower()
    if normalized == DRAFT_STATUS:
        return "pending"
    if normalized == ARCHIVED_STATUS:
        return "discarded"
    if normalized == ACTIVE_STATUS or normalized == STALE_STATUS:
        return "approved"
    if review_status in {"pending", "approved", "discarded"}:
        return review_status
    return "pending"


def _review_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"pending", "approved", "discarded"}:
        return normalized
    if normalized in {"active", "stale"}:
        return "approved"
    if normalized in {"archive", "archived", "rejected"}:
        return "discarded"
    return "pending"


def _memory_target_label(target: str) -> str:
    labels = {
        "fact": "Project fact",
        "user_profile": "User preference",
        "sop": "Operating note",
    }
    return labels.get(target, "Memory")


def _review_string_list(value: Any, *, limit: int) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        values = [value]
    result: list[str] = []
    for item in values:
        text = str(item).strip()
        if not text:
            continue
        result.append(text[:240])
        if len(result) >= limit:
            break
    return result


def _review_id_parts(value: str) -> tuple[str, str]:
    raw = value.strip()
    if not raw:
        return "", ""
    if ":" in raw:
        kind, item_id = raw.split(":", 1)
        kind = kind.strip().lower()
        item_id = item_id.strip()
        if kind in {"memory", "skill"}:
            return kind, item_id
        return "", item_id
    if raw.startswith("mem-"):
        return "memory", raw
    return "skill", raw


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _normalize_desktop_gateway_platform(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "qq": "qq",
        "qq_personal": "qq",
        "onebot": "qq",
        "napcat": "qq",
        "wechat": "wechat",
        "wechat_personal": "wechat",
        "wx": "wechat",
        "weixin": "wechat",
        "feishu": "feishu",
        "lark": "feishu",
        "fs": "feishu",
    }
    return aliases.get(normalized, "")


def _attachment_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _IMAGE_EXTENSIONS:
        return "image"
    if suffix in _TEXT_EXTENSIONS:
        return "text"
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        if mime.startswith("image/"):
            return "image"
        if mime.startswith("text/") or mime in {"application/json", "application/xml"}:
            return "text"
    return "file"


def _count_tool_turn_cards(history: list[dict[str, Any]]) -> int:
    count = 0
    for message in history:
        if str(message.get("role") or "").lower() != "assistant":
            continue
        blocks = message.get("blocks")
        if not isinstance(blocks, list):
            continue
        if any(
            isinstance(block, dict) and str(block.get("type") or "").lower() == "tool_use"
            for block in blocks
        ):
            count += 1
    return count


def _count_conversation_turns(history: list[dict[str, Any]]) -> int:
    turns = 0
    for message in history:
        if str(message.get("role") or "").lower() != "user":
            continue
        blocks = message.get("blocks")
        if isinstance(blocks, list) and any(
            isinstance(block, dict) and str(block.get("type") or "").lower() == "tool_result"
            for block in blocks
        ):
            continue
        if _message_visible_text(message):
            turns += 1
    return turns


def _message_visible_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    blocks = message.get("blocks")
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("text"), str):
            parts.append(str(block.get("text") or ""))
        elif isinstance(block.get("content"), str):
            parts.append(str(block.get("content") or ""))
    return "\n".join(part for part in parts if part).strip()


def _path_key(path: str | Path) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(Path(path).expanduser())


def _workspace_kind(path: Path) -> str:
    if path.is_dir():
        return "folder"
    suffix = path.suffix.lower()
    if suffix in _IMAGE_EXTENSIONS:
        return "image"
    if suffix in _TEXT_EXTENSIONS:
        return "text"
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        if mime.startswith("image/"):
            return "image"
        if mime.startswith("text/") or mime in {"application/json", "application/xml"}:
            return "text"
    return "file"


def _workspace_summary(path: Path, kind: str) -> str:
    if path.is_dir():
        try:
            count = sum(1 for _ in path.iterdir())
        except OSError:
            count = 0
        return f"{count} items"
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if kind == "text":
        return f"text, {_fmt_bytes(size)}"
    if kind == "image":
        return f"image, {_fmt_bytes(size)}"
    return f"file, {_fmt_bytes(size)}"


def _workspace_preview_text(path: Path) -> str:
    if not path.exists() or path.is_dir():
        return ""
    if _workspace_kind(path) == "image":
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) <= _WORKSPACE_PREVIEW_CHARS:
        return text.strip()
    return text[:_WORKSPACE_PREVIEW_CHARS].rstrip() + "\n...[preview truncated]"


def _fmt_bytes(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _normalize_permission_level(value: Any, fallback: str = "balanced") -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "strict": "locked",
        "safe": "locked",
        "ask": "locked",
        "normal": "balanced",
        "default": "balanced",
        "trusted": "full",
        "off": "full",
        "none": "full",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in {"locked", "balanced", "full"}:
        return normalized
    return fallback


def _session_title(history: list[dict[str, Any]]) -> str:
    for msg in history:
        if msg.get("role") != "user":
            continue
        for block in msg.get("blocks", []):
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text", "")).strip().replace("\n", " ")
                if text:
                    return text[:40] if len(text) > 40 else text
    return "Untitled session"


def _elapsed_ms(started: float) -> int:
    return int((datetime.now().timestamp() - started) * 1000)


def main() -> None:
    ElectronRuntime().serve()


if __name__ == "__main__":
    main()
