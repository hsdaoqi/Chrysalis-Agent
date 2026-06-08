"""SessionsMixin：拆分自 electron_runtime.py（方法体逐字符保留）。"""
from __future__ import annotations

from chrysalis.electron_runtime._common import *  # noqa: F401,F403


class SessionsMixin:
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
            "resumable_session": bool(active_id and self.kernel.session_store.has_checkpoint(active_id)),
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

    def _respond_review_snapshot(self, command: dict[str, Any]) -> None:
        snapshot = self._snapshot()
        self._respond(command, data=snapshot)
        self._emit_event("review_changed", snapshot=snapshot)

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

