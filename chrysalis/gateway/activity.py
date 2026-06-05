"""Shared gateway task activity for desktop snapshots.

Gateway adapters run in their own Python processes, so they cannot use the
Electron runtime's in-memory live task callbacks directly.  This module keeps a
small JSON file with the currently running gateway tasks.  The desktop runtime
polls that file and folds it into normal snapshots.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


STREAM_LIMIT = 12_000
EVENT_LIMIT = 100
TRACE_LIMIT = 240
ACTIVE_TTL_SECONDS = 30 * 60
LOCK_TIMEOUT_SECONDS = 2.0
LOCK_STALE_SECONDS = 15.0
WRITE_RETRY_ATTEMPTS = 12
WRITE_RETRY_DELAY_SECONDS = 0.05


class GatewayActivityStore:
    """Persist active gateway task state in a cross-process JSON file."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.last_error = ""

    def start_task(
        self,
        *,
        task_id: str,
        session_id: str,
        session_key: str,
        platform: str,
        source: dict[str, Any],
        task: str,
        model: str = "",
    ) -> None:
        now = _now()

        def mutate(data: dict[str, Any]) -> None:
            tasks = _tasks(data)
            tasks[task_id] = {
                "task_id": task_id,
                "session_id": session_id,
                "session_key": session_key,
                "platform": platform,
                "source": _safe_value(source),
                "status": "running",
                "status_text": "running",
                "active": True,
                "task_preview": _trim_text(task, 2_000),
                "model": model,
                "stream": "",
                "turn": 0,
                "events": [
                    {
                        "id": f"{task_id}-start",
                        "kind": "task_started",
                        "timestamp": now,
                        "task_id": task_id,
                        "session_id": session_id,
                        "task_preview": _trim_text(task, 2_000),
                    }
                ],
                "trace": [],
                "trace_seq": 0,
                "started_at": now,
                "updated_at": now,
                "updated_at_epoch": time.time(),
            }

        self._mutate(mutate)

    def update_session(self, task_id: str, session_id: str) -> None:
        if not task_id or not session_id:
            return

        def mutate(data: dict[str, Any]) -> None:
            task = _tasks(data).get(task_id)
            if isinstance(task, dict):
                task["session_id"] = session_id
                _touch(task)

        self._mutate(mutate)

    def status(self, task_id: str, message: str) -> None:
        message = str(message or "").strip()
        if not task_id or not message:
            return

        def mutate(data: dict[str, Any]) -> None:
            task = _tasks(data).get(task_id)
            if not isinstance(task, dict):
                return
            task["status_text"] = _trim_text(message, 600)
            _append_event(task, {"kind": "status", "status": task["status_text"]})
            _touch(task)

        self._mutate(mutate)

    def append_stream(self, task_id: str, chunk: str, *, kind: str = "stream") -> None:
        chunk = str(chunk or "")
        if not task_id or not chunk:
            return

        def mutate(data: dict[str, Any]) -> None:
            task = _tasks(data).get(task_id)
            if not isinstance(task, dict):
                return
            task["stream"] = _trim_left(str(task.get("stream") or "") + chunk, STREAM_LIMIT)
            task["stream_kind"] = kind
            _touch(task)

        self._mutate(mutate)

    def tool_started(self, task_id: str, tool: str, args: dict[str, Any] | None, turn: int) -> None:
        if not task_id:
            return

        def mutate(data: dict[str, Any]) -> None:
            task = _tasks(data).get(task_id)
            if not isinstance(task, dict):
                return
            resolved_turn = turn if turn > 0 else int(task.get("turn") or 0) + 1
            thought = str(task.get("stream") or "")
            task["turn"] = max(int(task.get("turn") or 0), resolved_turn)
            task["stream"] = ""
            _append_event(
                task,
                {
                    "kind": "tool_started",
                    "turn": resolved_turn,
                    "tool": str(tool or "tool"),
                    "args": _safe_value(args or {}),
                    "thought": _trim_text(thought, 2_000),
                },
            )
            _touch(task)

        self._mutate(mutate)

    def tool_completed(
        self,
        task_id: str,
        tool: str,
        observation: dict[str, Any] | None,
        turn: int,
    ) -> None:
        if not task_id:
            return

        def mutate(data: dict[str, Any]) -> None:
            task = _tasks(data).get(task_id)
            if not isinstance(task, dict):
                return
            resolved_turn = turn if turn > 0 else int(task.get("turn") or 0)
            _append_event(
                task,
                {
                    "kind": "tool_completed",
                    "turn": resolved_turn,
                    "tool": str(tool or "tool"),
                    "observation": _safe_value(observation or {}),
                },
            )
            _touch(task)

        self._mutate(mutate)

    def working_changed(self, task_id: str, snapshot: dict[str, Any]) -> None:
        if not task_id:
            return

        def mutate(data: dict[str, Any]) -> None:
            task = _tasks(data).get(task_id)
            if not isinstance(task, dict):
                return
            task["working"] = _safe_value(snapshot)
            _touch(task)

        self._mutate(mutate)

    def trace_event(self, task_id: str, payload: dict[str, Any]) -> None:
        if not task_id or not isinstance(payload, dict):
            return

        def mutate(data: dict[str, Any]) -> None:
            task = _tasks(data).get(task_id)
            if not isinstance(task, dict):
                return
            seq = int(task.get("trace_seq") or 0) + 1
            task["trace_seq"] = seq
            kind = str(payload.get("kind") or "event")
            node = {
                "id": f"{task_id}-{seq}-{kind}",
                "sequence": seq,
                "timestamp": _now(),
                "kind": kind,
                "session_id": str(task.get("session_id") or ""),
                "task_id": task_id,
                **_safe_value({key: value for key, value in payload.items() if key != "kind"}),
            }
            trace = task.get("trace")
            if not isinstance(trace, list):
                trace = []
                task["trace"] = trace
            trace.append(node)
            del trace[:-TRACE_LIMIT]
            _touch(task)

        self._mutate(mutate)

    def finish_task(self, task_id: str, result: dict[str, Any], *, status: str = "") -> None:
        if not task_id:
            return
        result = result if isinstance(result, dict) else {"final": str(result)}
        final = str(result.get("final") or result.get("question") or result.get("error") or "")
        resolved_status = status or _result_status(result)

        def mutate(data: dict[str, Any]) -> None:
            task = _tasks(data).get(task_id)
            if not isinstance(task, dict):
                return
            task["status"] = resolved_status
            task["status_text"] = resolved_status
            task["active"] = False
            task["result"] = _safe_value(result)
            task["final"] = _trim_text(final, 4_000)
            task["finished_at"] = _now()
            _append_event(
                task,
                {
                    "kind": "task_done",
                    "status": resolved_status,
                    "result": _safe_value(result),
                    "final": _trim_text(final, 4_000),
                },
            )
            _touch(task)

        self._mutate(mutate)

    def mark_session_stopping(self, session_id: str) -> None:
        if not session_id:
            return

        def mutate(data: dict[str, Any]) -> None:
            for task in _tasks(data).values():
                if not isinstance(task, dict) or str(task.get("session_id") or "") != session_id:
                    continue
                if str(task.get("status") or "") != "running":
                    continue
                task["cancel_requested"] = True
                task["status_text"] = "stopping"
                _append_event(task, {"kind": "status", "status": "stopping"})
                _touch(task)

        self._mutate(mutate)

    def snapshot(self, *, active_only: bool = True, ttl_seconds: int = ACTIVE_TTL_SECONDS) -> dict[str, Any]:
        now = time.time()
        changed = False
        with self._lock:
            data = self._read()
            items: list[dict[str, Any]] = []
            for task in _tasks(data).values():
                if not isinstance(task, dict):
                    continue
                active = _is_active_task(task, now=now, ttl_seconds=ttl_seconds)
                if active:
                    item = copy.deepcopy(task)
                    item["active"] = True
                    items.append(item)
                    continue
                if str(task.get("status") or "") == "running" and task.get("active") is not False:
                    task["status"] = "stale"
                    task["active"] = False
                    changed = True
                if not active_only:
                    item = copy.deepcopy(task)
                    item["active"] = False
                    items.append(item)
            if changed:
                try:
                    self._write(data)
                except OSError as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
            items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
            return {
                "version": int(data.get("version") or 1),
                "updated_at": str(data.get("updated_at") or ""),
                "activities": items,
            }

    def active_for_session(self, session_id: str) -> dict[str, Any] | None:
        if not session_id:
            return None
        for item in self.snapshot().get("activities", []):
            if str(item.get("session_id") or "") == session_id:
                return item
        return None

    def _mutate(self, callback) -> None:
        with self._lock:
            try:
                with self._file_lock():
                    data = self._read()
                    callback(data)
                    data["version"] = 1
                    data["updated_at"] = _now()
                    self._write(data)
            except OSError as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "updated_at": "", "tasks": {}}
        if not isinstance(data, dict):
            return {"version": 1, "updated_at": "", "tasks": {}}
        if not isinstance(data.get("tasks"), dict):
            data["tasks"] = {}
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, default=str)
        last_error: OSError | None = None
        for attempt in range(WRITE_RETRY_ATTEMPTS):
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
            try:
                tmp.write_text(payload, encoding="utf-8")
                os.replace(tmp, self.path)
                self.last_error = ""
                return
            except OSError as exc:
                last_error = exc
                if attempt < WRITE_RETRY_ATTEMPTS - 1:
                    time.sleep(WRITE_RETRY_DELAY_SECONDS * (attempt + 1))
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
        if last_error is not None:
            raise last_error

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        fd: int | None = None
        last_error: OSError | None = None
        started = time.monotonic()
        while fd is None:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            except FileExistsError:
                if _lock_is_stale(lock_path):
                    try:
                        lock_path.unlink()
                    except OSError:
                        pass
                    continue
                if time.monotonic() - started >= LOCK_TIMEOUT_SECONDS:
                    last_error = TimeoutError(f"timed out waiting for gateway activity lock: {lock_path}")
                    break
                time.sleep(0.025)
            except OSError as exc:
                last_error = exc
                break
        if fd is None:
            raise last_error or TimeoutError(f"could not acquire gateway activity lock: {lock_path}")
        try:
            yield
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                try:
                    lock_path.unlink()
                except OSError:
                    pass


def _tasks(data: dict[str, Any]) -> dict[str, Any]:
    tasks = data.get("tasks")
    if not isinstance(tasks, dict):
        tasks = {}
        data["tasks"] = tasks
    return tasks


def _append_event(task: dict[str, Any], payload: dict[str, Any]) -> None:
    events = task.get("events")
    if not isinstance(events, list):
        events = []
        task["events"] = events
    event = {
        "id": f"{task.get('task_id', 'gateway')}-{len(events) + 1}-{payload.get('kind', 'event')}",
        "timestamp": _now(),
        "task_id": str(task.get("task_id") or ""),
        "session_id": str(task.get("session_id") or ""),
        **payload,
    }
    events.append(event)
    del events[:-EVENT_LIMIT]


def _touch(task: dict[str, Any]) -> None:
    task["updated_at"] = _now()
    task["updated_at_epoch"] = time.time()


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _trim_text(value: Any, limit: int) -> str:
    return _trim_left(str(value or ""), limit)


def _trim_left(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _safe_value(value: Any, *, depth: int = 0, string_limit: int = 4_000) -> Any:
    if depth > 5:
        return _trim_text(value, string_limit)
    if isinstance(value, str):
        return _trim_text(value, string_limit)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return {
            str(key): _safe_value(item, depth=depth + 1, string_limit=string_limit)
            for key, item in value.items()
            if not str(key).startswith("_")
        }
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item, depth=depth + 1, string_limit=string_limit) for item in list(value)[:80]]
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except TypeError:
        return _trim_text(value, string_limit)


def _result_status(result: dict[str, Any]) -> str:
    if result.get("cancelled"):
        return "cancelled"
    if result.get("need_user"):
        return "waiting"
    if result.get("ok") is False or result.get("error"):
        return "error"
    return "done"


def _is_active_task(task: dict[str, Any], *, now: float, ttl_seconds: int) -> bool:
    if str(task.get("status") or "") != "running":
        return False
    if task.get("active") is False:
        return False
    updated_at_epoch = task.get("updated_at_epoch")
    try:
        updated = float(updated_at_epoch)
    except (TypeError, ValueError):
        return True
    return now - updated <= ttl_seconds


def _lock_is_stale(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime > LOCK_STALE_SECONDS
    except OSError:
        return False
