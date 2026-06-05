"""Persistent trace archive for the Electron desktop runtime."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_TRACE_LIMIT = 600
MAX_TRACE_STRING_CHARS = 4_000
MAX_TRACE_COLLECTION_ITEMS = 80
MAX_TRACE_DEPTH = 6


class TraceArchive:
    """Small JSON-backed archive keyed by session id.

    Trace events are intentionally stored per session. Individual nodes already
    carry task_id, so one session file can represent several completed tasks.
    """

    def __init__(self, root: Path, *, max_events: int = DEFAULT_TRACE_LIMIT) -> None:
        self.root = root
        self.max_events = max(1, int(max_events))
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    def load(self, session_id: str) -> list[dict[str, Any]]:
        session_id = str(session_id or "").strip()
        if not session_id:
            return []
        path = self._path_for_session(session_id)
        with self._lock:
            return self._load_events(path)

    def append(self, session_id: str, node: dict[str, Any]) -> list[dict[str, Any]]:
        session_id = str(session_id or "").strip()
        if not session_id or not isinstance(node, dict):
            return []
        path = self._path_for_session(session_id)
        with self._lock:
            events = self._load_events(path)
            event = _safe_trace_value(node)
            event_id = str(event.get("id") or "").strip()
            if event_id:
                events = [item for item in events if str(item.get("id") or "") != event_id]
            events.append(event)
            events = events[-self.max_events :]
            self._write_events(path, session_id, events)
            return events

    def delete(self, session_id: str) -> None:
        session_id = str(session_id or "").strip()
        if not session_id:
            return
        path = self._path_for_session(session_id)
        with self._lock:
            try:
                path.unlink()
            except FileNotFoundError:
                return

    def _path_for_session(self, session_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", session_id).strip("._-")
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
        stem = (safe[:80] or "session") + "-" + digest
        return self.root / f"{stem}.json"

    def _load_events(self, path: Path) -> list[dict[str, Any]]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return []
        raw_events = data.get("events") if isinstance(data, dict) else data
        if not isinstance(raw_events, list):
            return []
        events = [item for item in raw_events if isinstance(item, dict)]
        return events[-self.max_events :]

    def _write_events(self, path: Path, session_id: str, events: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "session_id": session_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "events": events[-self.max_events :],
        }
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            Path(tmp_name).replace(path)
        finally:
            try:
                Path(tmp_name).unlink()
            except FileNotFoundError:
                pass


def _safe_trace_value(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_TRACE_DEPTH:
        return _brief(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _brief(value) if isinstance(value, str) else value
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_TRACE_COLLECTION_ITEMS:
                safe["..."] = f"{len(value) - MAX_TRACE_COLLECTION_ITEMS} more fields"
                break
            safe[str(key)] = _safe_trace_value(item, depth=depth + 1)
        return safe
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        safe_items = [
            _safe_trace_value(item, depth=depth + 1)
            for item in items[:MAX_TRACE_COLLECTION_ITEMS]
        ]
        if len(items) > MAX_TRACE_COLLECTION_ITEMS:
            safe_items.append(f"... {len(items) - MAX_TRACE_COLLECTION_ITEMS} more items")
        return safe_items
    return _brief(value)


def _brief(value: Any) -> str:
    text = str(value)
    if len(text) <= MAX_TRACE_STRING_CHARS:
        return text
    return text[: MAX_TRACE_STRING_CHARS - 3].rstrip() + "..."
