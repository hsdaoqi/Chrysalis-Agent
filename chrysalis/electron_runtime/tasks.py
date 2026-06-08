"""TasksMixin：拆分自 electron_runtime.py（方法体逐字符保留）。"""
from __future__ import annotations

from chrysalis.electron_runtime._common import *  # noqa: F401,F403


class TasksMixin:
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

    def _resume_task(self, command: dict[str, Any]) -> None:
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
        if not self.kernel.session_store.has_checkpoint(session_id):
            self._respond(command, ok=False, error="No checkpoint to resume in this session.")
            return

        with self._state_lock:
            if session_id in self._running_tasks:
                self._respond(command, ok=False, error="A task is already running in this session.")
                return
            task_id = str(command.get("task_id") or uuid.uuid4())
            self._pending_user_actions.pop(session_id, None)
            task_kernel = Kernel(
                config=self.kernel.config,
                progress=lambda message, sid=session_id: self._on_progress(sid, message),
                session_id=session_id,
            )
            self._bind_task_callbacks(task_kernel, session_id, task_id)
            self._running_tasks[session_id] = _RunningTask(
                session_id=session_id,
                task_id=task_id,
                kernel=task_kernel,
                thread=threading.Thread(
                    target=self._task_worker,
                    args=(session_id, task_id, task_kernel, ""),
                    kwargs={"resume": True},
                    daemon=True,
                ),
                file_before={},
                workspace_before=self._snapshot_workspace_text_files(),
                emitted_diffs={},
                tool_turn=0,
            )
            self._store_draft(session_id, "")

        running = self._running_tasks[session_id]
        running.thread.start()
        self._respond(command, data={"started": True, "task_id": task_id, "session_id": session_id, "resumed": True})

    def _guide_task(self, command: dict[str, Any]) -> None:
        guidance = str(command.get("guidance") or command.get("text") or "").strip()
        if not guidance:
            self._respond(command, ok=False, error="Guidance is empty.")
            return

        session_id = str(command.get("session_id") or "").strip()
        if not session_id:
            session_id = self._active_session_id or self.kernel.session_store.current_id or ""
        if not session_id:
            self._respond(command, ok=False, error="No active session.")
            return

        task = self._running_tasks.get(session_id)
        if task is None:
            self._respond(command, ok=False, error="No running task in this session.")
            return
        if not task.kernel.guide(guidance):
            self._respond(command, ok=False, error="Guidance is empty.")
            return

        self._emit_event(
            "guidance",
            session_id=session_id,
            task_id=task.task_id,
            content=guidance,
        )
        self._respond(command, data={"guided": True, "task_id": task.task_id, "session_id": session_id})

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

    def _task_worker(self, session_id: str, task_id: str, kernel: Kernel, task: str, resume: bool = False) -> None:
        self._emit_event("task_started", session_id=session_id, task_id=task_id, status="thinking", snapshot=self._snapshot())
        self._emit_trace_node(
            session_id,
            task_id,
            "task_started",
            status="thinking",
            model=kernel.active_model_name,
            task_preview=(task[:320] if task else "[resume] 从断点续跑"),
            context=kernel.llm.context_usage(),
        )
        result: dict[str, Any]
        pending_user_action: dict[str, Any] | None = None
        try:
            result = kernel.resume() if resume else kernel.run(task)
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
                resumable=bool(result.get("resumable")),
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
        if result.get("resumable"):
            self._emit_event("task_resumable", session_id=session_id, task_id=task_id)

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

