"""Qt/QML backend bridge for Chrysalis desktop."""

from __future__ import annotations

import difflib
import json
import mimetypes
import queue
import re
import threading
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QAbstractTableModel, QModelIndex, QObject, Property, Qt, QTimer, QUrl, Signal, Slot

from configs.config import AgentConfig
from chrysalis.kernel import Kernel
from chrysalis.session_store import SessionStore
from chrysalis.llm.types import Usage
from chrysalis.llm.usage import _fmt_elapsed

_FILE_MODIFY_TOOLS = {"file_write", "file_patch"}
_MAX_ATTACHMENTS = 8
_ATTACHMENT_PREVIEW_CHARS = 8_000
_DESKTOP_SETTINGS_PATH = Path("data/desktop_settings.json")
_TEXT_EXTENSIONS = {
    ".bat", ".c", ".cfg", ".cpp", ".cs", ".css", ".csv", ".diff", ".env",
    ".go", ".h", ".hpp", ".htm", ".html", ".ini", ".java", ".js", ".json",
    ".jsx", ".log", ".lua", ".md", ".patch", ".php", ".ps1", ".py", ".qml",
    ".rs", ".sh", ".sql", ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml",
    ".yml",
}
_IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}


@dataclass
class DesktopMessage:
    kind: str
    role: str = ""
    content: str = ""
    title: str = ""
    summary: str = ""
    details: list[str] = field(default_factory=list)
    expanded: bool = False
    status: str = ""
    streaming: bool = False


@dataclass(frozen=True)
class DesktopAttachment:
    path: str
    name: str
    kind: str
    summary: str


class MessageListModel(QAbstractListModel):
    KindRole = Qt.UserRole + 1
    RoleRole = Qt.UserRole + 2
    ContentRole = Qt.UserRole + 3
    TitleRole = Qt.UserRole + 4
    SummaryRole = Qt.UserRole + 5
    DetailsRole = Qt.UserRole + 6
    ExpandedRole = Qt.UserRole + 7
    StatusRole = Qt.UserRole + 8
    StreamingRole = Qt.UserRole + 9

    def __init__(self) -> None:
        super().__init__()
        self._messages: list[DesktopMessage] = []

    def roleNames(self) -> dict[int, bytes]:
        return {
            self.KindRole: b"kind",
            self.RoleRole: b"role",
            self.ContentRole: b"content",
            self.TitleRole: b"title",
            self.SummaryRole: b"summary",
            self.DetailsRole: b"details",
            self.ExpandedRole: b"expanded",
            self.StatusRole: b"status",
            self.StreamingRole: b"streaming",
        }

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._messages)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._messages):
            return None
        msg = self._messages[row]
        if role == self.KindRole:
            return msg.kind
        if role == self.RoleRole:
            return msg.role
        if role == self.ContentRole:
            return msg.content
        if role == self.TitleRole:
            return msg.title
        if role == self.SummaryRole:
            return msg.summary
        if role == self.DetailsRole:
            return msg.details
        if role == self.ExpandedRole:
            return msg.expanded
        if role == self.StatusRole:
            return msg.status
        if role == self.StreamingRole:
            return msg.streaming
        return None

    def clear(self) -> None:
        if not self._messages:
            return
        self.beginResetModel()
        self._messages.clear()
        self.endResetModel()

    def set_messages(self, messages: list[DesktopMessage]) -> None:
        self.beginResetModel()
        self._messages = list(messages)
        self.endResetModel()

    def append(self, message: DesktopMessage) -> int:
        row = len(self._messages)
        self.beginInsertRows(QModelIndex(), row, row)
        self._messages.append(message)
        self.endInsertRows()
        return row

    def update_row(self, row: int, updater) -> bool:
        if row < 0 or row >= len(self._messages):
            return False
        self._messages[row] = updater(self._messages[row])
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, list(self.roleNames().keys()))
        return True

    @Slot(int)
    def toggle_expanded(self, row: int) -> None:
        self.update_row(
            row,
            lambda msg: DesktopMessage(
                kind=msg.kind,
                role=msg.role,
                content=msg.content,
                title=msg.title,
                summary=msg.summary,
                details=msg.details,
                expanded=not msg.expanded,
                status=msg.status,
                streaming=msg.streaming,
            ),
        )

class AttachmentListModel(QAbstractListModel):
    NameRole = Qt.UserRole + 1
    PathRole = Qt.UserRole + 2
    KindRole = Qt.UserRole + 3
    SummaryRole = Qt.UserRole + 4

    def __init__(self) -> None:
        super().__init__()
        self._attachments: list[DesktopAttachment] = []

    def roleNames(self) -> dict[int, bytes]:
        return {
            self.NameRole: b"name",
            self.PathRole: b"path",
            self.KindRole: b"kind",
            self.SummaryRole: b"summary",
        }

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._attachments)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._attachments):
            return None
        attachment = self._attachments[row]
        if role == self.NameRole:
            return attachment.name
        if role == self.PathRole:
            return attachment.path
        if role == self.KindRole:
            return attachment.kind
        if role == self.SummaryRole:
            return attachment.summary
        return None

    def items(self) -> list[DesktopAttachment]:
        return list(self._attachments)

    def append(self, attachment: DesktopAttachment) -> bool:
        if any(item.path == attachment.path for item in self._attachments):
            return False
        row = len(self._attachments)
        self.beginInsertRows(QModelIndex(), row, row)
        self._attachments.append(attachment)
        self.endInsertRows()
        return True

    def remove(self, row: int) -> bool:
        if row < 0 or row >= len(self._attachments):
            return False
        self.beginRemoveRows(QModelIndex(), row, row)
        del self._attachments[row]
        self.endRemoveRows()
        return True

    def clear(self) -> bool:
        if not self._attachments:
            return False
        self.beginResetModel()
        self._attachments.clear()
        self.endResetModel()
        return True


class SessionController(QObject):
    statusChanged = Signal()
    messagesChanged = Signal()
    busyChanged = Signal()
    titleChanged = Signal()
    modelNameChanged = Signal()

    def __init__(self, kernel: Kernel, session_id: str) -> None:
        super().__init__()
        self.kernel = kernel
        self.session_id = session_id
        self.title = ""
        self.model_name = self.kernel.active_model_name
        self.status = "ready"
        self.busy = False
        self.messages_model = MessageListModel()
        self._event_queue: queue.SimpleQueue[tuple[str, object]] = queue.SimpleQueue()
        self._stream_timer = QTimer(self)
        self._stream_timer.setInterval(33)
        self._stream_timer.timeout.connect(self._drain_events)
        self._worker: threading.Thread | None = None
        self._stream_row: int | None = None
        self._current_turn_row: int | None = None
        self._stream_buffer = ""
        self._turn = 0
        self._file_before: dict[str, str] = {}
        self._pending_config: AgentConfig | None = None
        self._bind_kernel()
        self._stream_timer.start()

    def _bind_kernel(self) -> None:
        self.kernel.loop.on_stream_chunk = self._on_stream_chunk
        self.kernel.loop.on_tool_call = self._on_tool_call
        self.kernel.loop.on_thinking = self._on_thinking
        if self.kernel.session_store.current_id == self.session_id:
            self.refresh_from_kernel()
        else:
            self.kernel.load_session(self.session_id)
            self.refresh_from_kernel()

    @Property(QObject, notify=messagesChanged)
    def messages_model_object(self) -> MessageListModel:
        return self.messages_model

    @Property(str, notify=statusChanged)
    def status_text(self) -> str:
        return self.status

    @Property(bool, notify=busyChanged)
    def busy_state(self) -> bool:
        return self.busy

    @Property(str, notify=titleChanged)
    def title_text(self) -> str:
        return self.title or "Untitled session"

    @Property(str, notify=modelNameChanged)
    def model_name_text(self) -> str:
        return self.model_name

    def refresh_from_kernel(self) -> None:
        self.messages_model.set_messages(self._history_messages(self.kernel.llm.history))
        self.title = self._session_title()
        self.model_name = self.kernel.active_model_name
        self.titleChanged.emit()
        self.modelNameChanged.emit()
        self.messagesChanged.emit()

    def apply_config(self, config: AgentConfig) -> None:
        if self.busy:
            self._pending_config = config
            return
        self.kernel = Kernel(config=config, progress=self.kernel.progress, session_id=self.session_id)
        self._pending_config = None
        self._bind_kernel()

    def run_task(self, task: str, display_task: str | None = None) -> dict:
        if self.busy:
            return {"ok": False, "error": "busy"}
        if self._pending_config is not None:
            self.apply_config(self._pending_config)
        task = task.strip()
        if not task:
            return {"ok": False, "error": "empty"}
        display_task = (display_task or task).strip()

        self._reset_turn_state()
        self.busy = True
        self.status = "thinking"
        self.messages_model.append(DesktopMessage(kind="user", role="user", content=display_task, title=">"))
        self.messages_model.append(DesktopMessage(kind="spacer"))
        self._emit_flags()

        def worker() -> None:
            try:
                result = self.kernel.run(task)
            except Exception as exc:  # pragma: no cover
                result = {"ok": False, "error": str(exc), "final": f"Exception: {exc}"}
            self._queue_event(("flush_stream", None))
            self._queue_event(("agent_done", result))
            self._queue_event(("refresh", None))
            self._queue_event(("status", "ready"))
            self._queue_event(("busy", False))

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()
        return {"ok": True, "started": True}

    def cancel_task(self) -> None:
        self.kernel.cancel()

    def load_session(self, session_id: str) -> None:
        self.session_id = session_id
        self.kernel.load_session(session_id)
        self.refresh_from_kernel()

    def new_session(self) -> str:
        self.session_id = self.kernel.new_session()
        self.messages_model.clear()
        self._append_banner()
        self.status = "ready"
        self.busy = False
        self._reset_runtime_state()
        self.title = self._session_title()
        self._emit_flags()
        return self.session_id

    def _queue_event(self, event: tuple[str, object]) -> None:
        self._event_queue.put(event)

    def _drain_events(self) -> None:
        changed_messages = False
        changed_status = False
        changed_busy = False
        while True:
            try:
                kind, payload = self._event_queue.get_nowait()
            except Exception:
                break

            if kind == "stream":
                chunk = str(payload)
                if chunk:
                    self._append_stream(chunk)
                    changed_messages = True
            elif kind == "flush_stream":
                self._remove_stream_row()
                changed_messages = True
            elif kind == "tool_started":
                tool, args = payload  # type: ignore[misc]
                self._remove_stream_row(clear_buffer=False)
                self._start_turn(str(tool), args if isinstance(args, dict) else {})
                changed_messages = True
            elif kind == "tool_completed":
                tool, args, observation = payload  # type: ignore[misc]
                self._complete_turn(str(tool), args if isinstance(args, dict) else {}, observation)
                changed_messages = True
            elif kind == "file_diff":
                path, before, after = payload  # type: ignore[misc]
                self._append_diff(str(path), str(before), str(after))
                changed_messages = True
            elif kind == "thinking":
                self._append_thinking(str(payload))
                changed_messages = True
            elif kind == "agent_done":
                self._append_agent_done(payload if isinstance(payload, dict) else {})
                changed_messages = True
            elif kind == "refresh":
                self.refresh_from_kernel()
                changed_messages = True
            elif kind == "status":
                self.status = str(payload)
                changed_status = True
            elif kind == "busy":
                self.busy = bool(payload)
                changed_busy = True

        if changed_messages:
            self.messagesChanged.emit()
        if changed_status:
            self.statusChanged.emit()
        if changed_busy:
            self.busyChanged.emit()

    def _on_stream_chunk(self, chunk: str) -> None:
        if chunk:
            self._queue_event(("stream", chunk))

    def _on_tool_call(self, tool: str, args: dict, observation: dict | None) -> None:
        if observation is None:
            self._capture_file_before(tool, args)
            self._queue_event(("status", f"executing {tool}"))
            self._queue_event(("tool_started", (tool, args)))
        else:
            self._queue_event(("tool_completed", (tool, args, observation)))
            diff_payload = self._make_file_diff_payload(tool, args, observation)
            if diff_payload is not None:
                self._queue_event(("file_diff", diff_payload))
            self._queue_event(("status", "thinking"))

    def _on_thinking(self, text: str) -> None:
        if text:
            self._queue_event(("thinking", text))

    def _append_banner(self) -> None:
        self.messages_model.append(DesktopMessage(kind="system", content="Chrysalis v0.1 / autonomous agent"))
        self.messages_model.append(DesktopMessage(kind="system", content="Type a task, or start a new session."))
        self.messages_model.append(DesktopMessage(kind="spacer"))

    def _history_messages(self, history: list[dict]) -> list[DesktopMessage]:
        messages = [DesktopMessage(kind="system", content="-- conversation history --"), DesktopMessage(kind="spacer")]
        turn = 0
        last_turn_index: int | None = None
        for msg in history:
            role = str(msg.get("role", ""))
            blocks = msg.get("blocks", [])
            if not isinstance(blocks, list):
                continue
            if role == "user":
                has_tool_result = any(
                    isinstance(block, dict) and block.get("type") == "tool_result"
                    for block in blocks
                )
                text_parts: list[str] = []
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text = self._strip_session_context(str(block.get("text", "")))
                        if text:
                            text_parts.append(text)
                    elif block.get("type") == "tool_result":
                        result = self._format_history_tool_result(block)
                        if result:
                            if last_turn_index is not None:
                                messages[last_turn_index].details.append(result)
                            elif result:
                                messages.append(DesktopMessage(kind="turn", title="Turn ? - ok tool_result - history", summary="history", details=[result], status="ok"))
                                last_turn_index = len(messages) - 1
                user_text = "\n".join(text_parts).strip()
                if user_text and not has_tool_result:
                    messages.append(DesktopMessage(kind="user", role="user", content=user_text, title=">"))
                    messages.append(DesktopMessage(kind="spacer"))
                continue
            if role == "assistant":
                final_parts: list[str] = []
                thinking_parts: list[str] = []
                tool_blocks: list[dict] = []
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text = self._normalize_final_text(str(block.get("text", "")))
                        if text:
                            final_parts.append(text)
                    elif btype == "thinking":
                        thinking = self._compact_text(str(block.get("text", "")), 300)
                        if thinking:
                            thinking_parts.append(f"Thought: {thinking}")
                    elif btype == "tool_use":
                        tool_blocks.append(block)
                for block in tool_blocks:
                    turn += 1
                    tool = str(block.get("name") or "tool")
                    args = self._parse_args(block.get("arguments", ""))
                    detail = [f"Args: {self._fmt_args_full(args)}"]
                    if thinking_parts:
                        detail = thinking_parts[:1] + detail
                        thinking_parts = thinking_parts[1:]
                    messages.append(DesktopMessage(kind="turn", title=self._turn_title(turn, "ok", tool, self._fmt_args(args), "history"), summary="history", details=detail, expanded=False, status="ok"))
                    last_turn_index = len(messages) - 1
                final = "\n\n".join(final_parts).strip()
                if final:
                    messages.append(DesktopMessage(kind="final", role="assistant", content=final, title="Chrysalis"))
                    messages.append(DesktopMessage(kind="spacer"))
        messages.append(DesktopMessage(kind="system", content="-- end history --"))
        messages.append(DesktopMessage(kind="spacer"))
        return messages

    def _append_stream(self, chunk: str) -> None:
        self._stream_buffer += chunk
        if self._stream_row is None:
            self._stream_row = self.messages_model.append(DesktopMessage(kind="stream", content=self._stream_buffer, streaming=True))
            return
        self.messages_model.update_row(self._stream_row, lambda msg: DesktopMessage(kind="stream", content=self._stream_buffer, streaming=True))

    def _remove_stream_row(self, clear_buffer: bool = True) -> None:
        if clear_buffer:
            self._stream_buffer = ""
        row = self._stream_row
        self._stream_row = None
        if row is None or row < 0 or row >= self.messages_model.rowCount():
            return
        self.messages_model.beginRemoveRows(QModelIndex(), row, row)
        del self.messages_model._messages[row]
        self.messages_model.endRemoveRows()

    def _start_turn(self, tool: str, args: dict) -> None:
        self._turn += 1
        details = []
        if self._stream_buffer:
            details.append(f"Thought: {self._compact_text(self._stream_buffer, 220)}")
        details.append(f"Args: {self._fmt_args_full(args)}")
        self._stream_buffer = ""
        self._current_turn_row = self.messages_model.append(
            DesktopMessage(kind="turn", title=self._turn_title(self._turn, "running", tool, self._fmt_args(args), ""), details=details, expanded=False, status="running")
        )

    def _complete_turn(self, tool: str, args: dict, observation: object) -> None:
        obs = observation if isinstance(observation, dict) else {}
        ok = bool(obs.get("ok", False))
        summary = self._obs_summary(obs)
        row = self._current_turn_row
        if row is None:
            self._turn += 1
            row = self.messages_model.append(
                DesktopMessage(kind="turn", title=self._turn_title(self._turn, "running", tool, self._fmt_args(args), ""), details=[f"Args: {self._fmt_args_full(args)}"], expanded=False, status="running")
            )
        extra_lines: list[str] = []
        if ok:
            content = self._obs_content(obs)
            if content:
                extra_lines.append("Result:")
                for line in content.split("\n")[:30]:
                    extra_lines.append(f"  {line}")
                if content.count("\n") > 30:
                    extra_lines.append("  ... truncated")
        else:
            extra_lines.append(f"Error: {obs.get('error', '')}")
        status = "ok" if ok else "error"
        self.messages_model.update_row(
            row,
            lambda msg: DesktopMessage(kind="turn", title=self._turn_title(self._turn, status, tool, self._fmt_args(args), summary), summary=summary, details=msg.details + extra_lines, expanded=msg.expanded, status=status),
        )
        self._current_turn_row = None

    def _append_thinking(self, text: str) -> None:
        line = f"Thought: {self._compact_text(text, 300)}"
        row = self._current_turn_row
        if row is None:
            self._append_stream(text)
            return
        self.messages_model.update_row(
            row,
            lambda msg: DesktopMessage(kind=msg.kind, role=msg.role, content=msg.content, title=msg.title, summary=msg.summary, details=msg.details + [line], expanded=msg.expanded, status=msg.status, streaming=msg.streaming),
        )

    def _append_diff(self, path: str, before: str, after: str) -> None:
        row = self._current_turn_row
        if row is None:
            for idx in range(self.messages_model.rowCount() - 1, -1, -1):
                if self.messages_model._messages[idx].kind == "turn":
                    row = idx
                    break
        if row is None:
            return
        lines = [f"Diff: {path}"] + [f"  {line}" for line in self._make_diff(before, after)[:25]]
        self.messages_model.update_row(
            row,
            lambda msg: DesktopMessage(kind=msg.kind, role=msg.role, content=msg.content, title=msg.title, summary=msg.summary, details=msg.details + lines, expanded=msg.expanded, status=msg.status, streaming=msg.streaming),
        )

    def _append_agent_done(self, result: dict) -> None:
        final = self._normalize_final_text(str(result.get("final") or result.get("error") or ""))
        if final:
            self.messages_model.append(DesktopMessage(kind="spacer"))
            self.messages_model.append(DesktopMessage(kind="final", role="assistant", content=final, title="Chrysalis"))
        usage_line = self._format_usage(result)
        if usage_line:
            self.messages_model.append(DesktopMessage(kind="usage", content=usage_line))
        if result.get("need_user"):
            self.messages_model.append(DesktopMessage(kind="warning", content="Waiting for input..."))
        self.messages_model.append(DesktopMessage(kind="spacer"))
        self._reset_turn_state()
        self.refresh_from_kernel()

    def _capture_file_before(self, tool: str, args: dict) -> None:
        if tool not in _FILE_MODIFY_TOOLS:
            return
        path_str = str(args.get("path", ""))
        if not path_str:
            return
        workspace = self.kernel.config.workspace_dir
        target = Path(path_str)
        if not target.is_absolute():
            target = workspace / target
        try:
            self._file_before[path_str] = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self._file_before[path_str] = ""

    def _make_file_diff_payload(self, tool: str, args: dict, obs: dict) -> tuple[str, str, str] | None:
        if tool not in _FILE_MODIFY_TOOLS or not obs.get("ok"):
            return None
        path_str = str(args.get("path", ""))
        before = self._file_before.pop(path_str, "")
        resolved = obs.get("path", path_str)
        try:
            after = Path(str(resolved)).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        if before == after:
            return None
        return path_str, before, after

    def _reset_runtime_state(self) -> None:
        self._reset_turn_state()
        self._file_before.clear()

    def _reset_turn_state(self) -> None:
        self._stream_row = None
        self._stream_buffer = ""
        self._turn = 0
        self._current_turn_row = None

    def _emit_flags(self) -> None:
        self.statusChanged.emit()
        self.busyChanged.emit()
        self.messagesChanged.emit()

    def _session_title(self) -> str:
        for item in self.kernel.list_sessions():
            if item.get("id") == self.session_id:
                return str(item.get("title") or "Untitled session")
        return "Untitled session"

    def _turn_title(self, turn: int, status: str, tool: str, args_brief: str, summary: str) -> str:
        if status == "running":
            return f"Turn {turn} - * {tool}({args_brief}) ..."
        if status == "ok":
            return f"Turn {turn} - ok {tool} - {summary}"
        return f"Turn {turn} - error {tool} - {summary}"

    def _fmt_args(self, args: dict) -> str:
        parts = []
        for key, value in list(args.items())[:2]:
            text = str(value)
            if len(text) > 25:
                text = text[:22] + "..."
            parts.append(f'{key}="{text}"')
        return ", ".join(parts)

    def _fmt_args_full(self, args: dict) -> str:
        return json.dumps(args, ensure_ascii=False, indent=2)[:300]

    def _parse_args(self, raw: object) -> dict:
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            parsed = json.loads(str(raw))
        except json.JSONDecodeError:
            return {"raw": str(raw)}
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    def _obs_summary(self, obs: dict) -> str:
        if "content" in obs:
            content = str(obs["content"])
            lines = content.count("\n") + 1
            return f"{lines} lines" if lines > 3 else content[:40].replace("\n", " ")
        if "stdout" in obs:
            return str(obs["stdout"])[:40].replace("\n", " ")
        if "entries" in obs:
            return f"{len(obs['entries'])} items"
        if "path" in obs:
            return str(obs["path"])
        return "done"

    def _obs_content(self, obs: dict) -> str:
        if "content" in obs:
            return str(obs["content"])
        if "stdout" in obs:
            return str(obs["stdout"])
        return ""

    def _format_history_tool_result(self, block: dict) -> str:
        content = self._compact_text(str(block.get("content", "")), 500)
        if not content:
            return "Result:"
        if bool(block.get("is_error", False)):
            return f"Error: {content}"
        return f"Result: {content}"

    def _make_diff(self, before: str, after: str) -> list[str]:
        return [line.rstrip() for line in difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), lineterm="")]

    def _format_usage(self, result: dict) -> str:
        usage = result.get("usage")
        if not usage or not usage.get("total_tokens"):
            return ""
        u = Usage.from_dict(usage)
        elapsed = result.get("elapsed_ms", 0)
        cost = usage.get("cost", 0)
        turns = usage.get("turns", 0)
        parts = [u.format()]
        if cost > 0:
            parts.append(f"~${cost:.4f}")
        if turns:
            parts.append(f"{turns} turns")
        if elapsed:
            parts.append(_fmt_elapsed(elapsed))
        return f"[{' | '.join(parts)}]"

    def _strip_session_context(self, text: str) -> str:
        text = self._normalize_final_text(text)
        markers = [r"\[SESSION CONTEXT\]", r"<recent_turns>"]
        for marker in markers:
            match = re.search(marker, text, flags=re.I)
            if match:
                text = text[: match.start()]
                break
        return text.strip()

    def _normalize_final_text(self, text: str) -> str:
        text = str(text).replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"<summary>.*?</summary>", "", text, flags=re.I | re.S)
        text = re.sub(r"</?summary>", "", text, flags=re.I)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _compact_text(self, text: str, limit: int) -> str:
        text = " ".join(str(text).split())
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."


class DesktopSessionModel(QAbstractTableModel):
    SessionIdRole = Qt.UserRole + 1
    TitleRole = Qt.UserRole + 2
    UpdatedAtRole = Qt.UserRole + 3
    BusyRole = Qt.UserRole + 4
    ModelNameRole = Qt.UserRole + 5
    ControllerRole = Qt.UserRole + 6
    PinnedRole = Qt.UserRole + 7

    def __init__(self, controllers: list[SessionController]) -> None:
        super().__init__()
        self._controllers = controllers

    def roleNames(self) -> dict[int, bytes]:
        return {
            self.SessionIdRole: b"sessionId",
            self.TitleRole: b"title",
            self.UpdatedAtRole: b"updatedAt",
            self.BusyRole: b"busy",
            self.ModelNameRole: b"modelName",
            self.ControllerRole: b"controller",
            self.PinnedRole: b"pinned",
        }

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._controllers)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 1

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._controllers):
            return None
        controller = self._controllers[row]
        if role == self.SessionIdRole:
            return controller.session_id
        if role == self.TitleRole:
            return controller.title_text
        if role == self.UpdatedAtRole:
            for item in controller.kernel.list_sessions():
                if item.get("id") == controller.session_id:
                    return item.get("updated_at", "")
            return ""
        if role == self.BusyRole:
            return controller.busy_state
        if role == self.ModelNameRole:
            return controller.model_name_text
        if role == self.ControllerRole:
            return controller
        if role == self.PinnedRole:
            for item in controller.kernel.list_sessions():
                if item.get("id") == controller.session_id:
                    return bool(item.get("pinned", False))
            return False
        return None


class DesktopBackend(QObject):
    statusChanged = Signal()
    sessionsChanged = Signal()
    modelNameChanged = Signal()
    activeSessionChanged = Signal()
    recoveryChanged = Signal()
    attachmentsChanged = Signal()
    settingsChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.config = AgentConfig()
        self._session_store = SessionStore(self.config.data_dir / "sessions")
        self._controllers: list[SessionController] = []
        self._active_index = 0
        self._session_filter = ""
        self._session_model = DesktopSessionModel(self._controllers)
        self._attachment_model = AttachmentListModel()
        self._recovery_path = self.config.data_dir / "desktop_recovery.json"
        self._settings_path = self.config.data_dir / "desktop_settings.json"
        self._settings = self._load_settings()
        self._draft_text = self._load_recovery_text()
        self._load_sessions()

    @Property(QObject, notify=sessionsChanged)
    def sessions_model_object(self) -> DesktopSessionModel:
        return self._session_model

    @Property(QObject, notify=activeSessionChanged)
    def active_session_object(self) -> SessionController:
        if not self._controllers:
            self._ensure_session()
        return self._controllers[self._active_index]

    @Property(int, notify=activeSessionChanged)
    def active_session_index(self) -> int:
        return self._active_index

    @Property(str, notify=statusChanged)
    def status_text(self) -> str:
        return self.active_session_object.status_text

    @Property(str, notify=modelNameChanged)
    def model_name_text(self) -> str:
        return self.active_session_object.model_name_text

    @Property(bool, notify=statusChanged)
    def busy_state(self) -> bool:
        return self.active_session_object.busy_state

    @Property(str, notify=recoveryChanged)
    def draft_text(self) -> str:
        return self._draft_text

    @Property(QObject, notify=attachmentsChanged)
    def attachments_model_object(self) -> AttachmentListModel:
        return self._attachment_model

    @Property(int, notify=attachmentsChanged)
    def attachment_count(self) -> int:
        return self._attachment_model.rowCount()

    @Property(str, notify=settingsChanged)
    def settings_json(self) -> str:
        return json.dumps(self._settings, ensure_ascii=False, indent=2)

    @Property(str, notify=settingsChanged)
    def settings_llm_json(self) -> str:
        return json.dumps(self._settings.get("llm", {}), ensure_ascii=False, indent=2)

    @Property(str, notify=settingsChanged)
    def system_prompt_text(self) -> str:
        return str(self._settings.get("system_prompt") or "")

    @Slot()
    def refresh_sessions(self) -> None:
        self._reload_controllers()
        self.sessionsChanged.emit()
        self.activeSessionChanged.emit()
        self.statusChanged.emit()

    @Slot()
    def new_session(self) -> str:
        controller = self._ensure_session()
        sid = controller.new_session()
        self._active_index = self._controllers.index(controller)
        self.activeSessionChanged.emit()
        self.sessionsChanged.emit()
        self.statusChanged.emit()
        return sid

    @Slot(str)
    def load_session(self, session_id: str) -> None:
        controller = self._find_controller(session_id)
        if controller is None:
            controller = self._create_controller(session_id)
        self._active_index = self._controllers.index(controller)
        self.activeSessionChanged.emit()
        self.statusChanged.emit()
        self.sessionsChanged.emit()

    @Slot(str)
    def delete_session(self, session_id: str) -> bool:
        controller = self._find_controller(session_id)
        if controller is not None and controller.busy_state:
            controller.cancel_task()
        if not self._session_store.delete(session_id):
            return False
        self._controllers = [c for c in self._controllers if c.session_id != session_id]
        if not self._controllers:
            self._ensure_session()
        if self._active_index >= len(self._controllers):
            self._active_index = max(0, len(self._controllers) - 1)
        self._session_model = DesktopSessionModel(self._controllers)
        self.sessionsChanged.emit()
        self.activeSessionChanged.emit()
        self.statusChanged.emit()
        return True

    @Slot(str)
    def run_task(self, task: str) -> dict:
        attachments = self._attachment_model.items()
        task_with_attachments = self._compose_task_with_attachments(task, attachments)
        if not task_with_attachments.strip():
            return {"ok": False, "error": "empty"}
        display_task = self._compose_display_task(task, attachments)
        result = self.active_session_object.run_task(task_with_attachments, display_task=display_task)
        if result.get("ok"):
            self.save_draft("")
            if self._attachment_model.clear():
                self.attachmentsChanged.emit()
        return result

    @Slot()
    def cancel_active_task(self) -> None:
        self.active_session_object.cancel_task()

    @Slot()
    def clear_draft(self) -> None:
        self.save_draft("")

    @Slot(str, result=bool)
    def add_attachment(self, path_or_url: str) -> bool:
        if self._attachment_model.rowCount() >= _MAX_ATTACHMENTS:
            return False
        path = self._attachment_path(path_or_url)
        if path is None or not path.is_file():
            return False
        attachment = self._make_attachment(path)
        if not self._attachment_model.append(attachment):
            return False
        self.attachmentsChanged.emit()
        return True

    @Slot(str, result=int)
    def add_attachments(self, raw_paths: str) -> int:
        added = 0
        for value in raw_paths.splitlines():
            if self.add_attachment(value.strip()):
                added += 1
        return added

    @Slot(int)
    def remove_attachment(self, row: int) -> None:
        if self._attachment_model.remove(row):
            self.attachmentsChanged.emit()

    @Slot()
    def clear_attachments(self) -> None:
        if self._attachment_model.clear():
            self.attachmentsChanged.emit()

    @Slot(result=str)
    def load_settings_text(self) -> str:
        return self.settings_json

    @Slot(result=str)
    def load_llm_settings_text(self) -> str:
        return self.settings_llm_json

    @Slot(result=str)
    def load_system_prompt(self) -> str:
        return self.system_prompt_text

    @Slot(str, result=bool)
    def save_settings_text(self, raw: str) -> bool:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return False
        if not isinstance(data, dict):
            return False
        self._settings = self._normalize_settings(data)
        self._save_settings()
        self.settingsChanged.emit()
        self._reload_app_config()
        return True

    @Slot(str, result=bool)
    def save_llm_settings_text(self, raw: str) -> bool:
        try:
            llm = json.loads(raw)
        except json.JSONDecodeError:
            return False
        if not isinstance(llm, dict):
            return False
        self._settings["enabled"] = True
        self._settings["llm"] = self._normalize_llm_settings(llm)
        self._save_settings()
        self.settingsChanged.emit()
        self._reload_app_config()
        return True

    @Slot(str, result=bool)
    def save_system_prompt(self, text: str) -> bool:
        self._settings["enabled"] = True
        self._settings["system_prompt"] = text
        self._save_settings()
        self.settingsChanged.emit()
        self._reload_app_config()
        return True

    @Slot()
    def reset_settings(self) -> None:
        self._settings = {"enabled": False, "llm": {}, "system_prompt": ""}
        self._save_settings()
        self.settingsChanged.emit()
        self._reload_app_config()

    @Slot()
    def reload_active_session_config(self) -> None:
        self._reload_app_config()

    @Slot(str)
    def save_draft(self, text: str) -> None:
        self._draft_text = text
        data = {
            "text": text,
            "session_id": self.active_session_object.session_id if self._controllers else "",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            self._recovery_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        self.recoveryChanged.emit()

    @Slot(str, str)
    def rename_session(self, session_id: str, title: str) -> bool:
        ok = self._session_store.rename(session_id, title)
        if ok:
            controller = self._find_controller(session_id)
            if controller is not None:
                controller.title = title.strip()
                controller.titleChanged.emit()
            self.refresh_sessions()
        return ok

    @Slot(str, bool)
    def set_session_pinned(self, session_id: str, pinned: bool) -> bool:
        ok = self._session_store.set_pinned(session_id, pinned)
        if ok:
            self._reload_controllers()
            self.sessionsChanged.emit()
            self.activeSessionChanged.emit()
        return ok

    @Slot(str)
    def toggle_session_pinned(self, session_id: str) -> bool:
        pinned = False
        for item in self._session_store.list_sessions(limit=200):
            if item.get("id") == session_id:
                pinned = bool(item.get("pinned", False))
                break
        return self.set_session_pinned(session_id, not pinned)

    @Slot(str)
    def set_session_filter(self, query: str) -> None:
        self._session_filter = query.strip().lower()
        self._reload_controllers()
        self.sessionsChanged.emit()
        self.activeSessionChanged.emit()

    @Slot(int)
    def activate_session_row(self, row: int) -> None:
        if row < 0 or row >= len(self._controllers):
            return
        self._active_index = row
        self.activeSessionChanged.emit()
        self.statusChanged.emit()

    def _load_sessions(self) -> None:
        sessions = self._filtered_sessions()
        if not sessions:
            controller = self._create_controller_from_new_session()
            self._active_index = 0
            self.sessionsChanged.emit()
            self.activeSessionChanged.emit()
            self.statusChanged.emit()
            return
        for entry in sessions:
            self._controllers.append(self._create_controller(entry["id"]))
        self._session_model = DesktopSessionModel(self._controllers)
        self._active_index = 0

    def _ensure_session(self) -> SessionController:
        if self._controllers:
            return self._controllers[self._active_index]
        controller = self._create_controller_from_new_session()
        self._controllers.append(controller)
        self._session_model = DesktopSessionModel(self._controllers)
        self._active_index = 0
        self.sessionsChanged.emit()
        self.activeSessionChanged.emit()
        return controller

    def _create_controller_from_new_session(self) -> SessionController:
        kernel = Kernel(progress=self._make_progress_handler())
        sid = kernel.session_store.current_id or kernel.new_session()
        controller = SessionController(kernel, sid)
        controller.messagesChanged.connect(self.sessionsChanged.emit)
        controller.statusChanged.connect(self.statusChanged.emit)
        controller.busyChanged.connect(self.statusChanged.emit)
        controller.titleChanged.connect(self.sessionsChanged.emit)
        controller.modelNameChanged.connect(self.modelNameChanged.emit)
        return controller

    def _create_controller(self, session_id: str) -> SessionController:
        kernel = Kernel(progress=self._make_progress_handler(), session_id=session_id)
        controller = SessionController(kernel, session_id)
        controller.messagesChanged.connect(self.sessionsChanged.emit)
        controller.statusChanged.connect(self.statusChanged.emit)
        controller.busyChanged.connect(self.statusChanged.emit)
        controller.titleChanged.connect(self.sessionsChanged.emit)
        controller.modelNameChanged.connect(self.modelNameChanged.emit)
        return controller

    def _find_controller(self, session_id: str) -> SessionController | None:
        for controller in self._controllers:
            if controller.session_id == session_id:
                return controller
        return None

    def _reload_controllers(self) -> None:
        sessions = self._filtered_sessions()
        existing = {controller.session_id: controller for controller in self._controllers}
        new_controllers: list[SessionController] = []
        for entry in sessions:
            sid = str(entry.get("id", ""))
            controller = existing.get(sid)
            if controller is None:
                controller = self._create_controller(sid)
            else:
                controller.refresh_from_kernel()
            new_controllers.append(controller)
        self._controllers = new_controllers
        self._session_model = DesktopSessionModel(self._controllers)
        if self._controllers:
            self._active_index = min(self._active_index, len(self._controllers) - 1)
        else:
            self._active_index = 0

    def _filtered_sessions(self) -> list[dict]:
        sessions = self._session_store.list_sessions(limit=200)
        if not self._session_filter:
            return sessions
        query = self._session_filter
        return [
            session for session in sessions
            if query in str(session.get("title", "")).lower()
            or query in str(session.get("model", "")).lower()
            or query in str(session.get("id", "")).lower()
        ]

    def _load_recovery_text(self) -> str:
        try:
            data = json.loads(self._recovery_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        return str(data.get("text") or "")

    def _load_settings(self) -> dict:
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"enabled": False, "llm": {}, "system_prompt": ""}
        return self._normalize_settings(data)

    def _normalize_settings(self, data: dict) -> dict:
        return {
            "enabled": bool(data.get("enabled", False)),
            "llm": self._normalize_llm_settings(data.get("llm", {})),
            "system_prompt": str(data.get("system_prompt") or ""),
        }

    def _normalize_llm_settings(self, data: dict) -> dict:
        return {
            "name": str(data.get("name") or ""),
            "provider": str(data.get("provider") or "openai"),
            "api_key": str(data.get("api_key") or ""),
            "base_url": str(data.get("base_url") or ""),
            "model": str(data.get("model") or ""),
            "context_window": int(data.get("context_window", 28000) or 28000),
            "temperature": float(data.get("temperature", 0.2) or 0.2),
            "max_tokens": int(data.get("max_tokens")) if data.get("max_tokens") not in (None, "") else None,
            "max_retries": int(data.get("max_retries", 4) or 4),
            "timeout": int(data.get("timeout", 60) or 60),
            "proxy": str(data.get("proxy") or ""),
            "thinking": str(data.get("thinking") or "disabled"),
            "thinking_budget": int(data.get("thinking_budget")) if data.get("thinking_budget") not in (None, "") else None,
        }

    def _save_settings(self) -> None:
        data = self._settings
        try:
            self._settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _reload_app_config(self) -> None:
        self.config = AgentConfig()
        self._session_store = SessionStore(self.config.data_dir / "sessions")
        self._session_model = DesktopSessionModel(self._controllers)
        for controller in self._controllers:
            controller.apply_config(self.config)
        self.sessionsChanged.emit()
        self.activeSessionChanged.emit()
        self.statusChanged.emit()
        self.modelNameChanged.emit()


    def _attachment_path(self, path_or_url: str) -> Path | None:
        raw = path_or_url.strip()
        if not raw:
            return None
        url = QUrl(raw)
        if url.isValid() and url.isLocalFile():
            raw = url.toLocalFile()
        elif raw.startswith("file:///"):
            raw = raw[8:]
        try:
            return Path(raw).expanduser().resolve()
        except OSError:
            return None

    def _make_attachment(self, path: Path) -> DesktopAttachment:
        kind = self._attachment_kind(path)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        summary = f"{kind} file, {self._format_bytes(size)}"
        return DesktopAttachment(
            path=str(path),
            name=path.name,
            kind=kind,
            summary=summary,
        )

    def _attachment_kind(self, path: Path) -> str:
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

    def _compose_task_with_attachments(self, task: str, attachments: list[DesktopAttachment]) -> str:
        task = task.strip()
        if not attachments:
            return task
        if not task:
            task = "请处理我附加的文件。"
        sections = [task, "", "[ATTACHMENTS]"]
        for index, attachment in enumerate(attachments, 1):
            sections.append(f"{index}. {attachment.name}")
            sections.append(f"   path: {attachment.path}")
            sections.append(f"   type: {attachment.kind}")
            sections.append(f"   summary: {attachment.summary}")
            preview = self._attachment_preview(attachment)
            if preview:
                sections.append("   preview:")
                sections.append(self._indent(preview, "     "))
        sections.append("[/ATTACHMENTS]")
        return "\n".join(sections).strip()

    def _compose_display_task(self, task: str, attachments: list[DesktopAttachment]) -> str:
        task = task.strip()
        if not attachments:
            return task
        lines = [task or "请处理我附加的文件。", "", "Attachments:"]
        for attachment in attachments:
            lines.append(f"- {attachment.name} ({attachment.kind})")
        return "\n".join(lines).strip()

    def _attachment_preview(self, attachment: DesktopAttachment) -> str:
        if attachment.kind != "text":
            return ""
        path = Path(attachment.path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        if len(text) <= _ATTACHMENT_PREVIEW_CHARS:
            return text.strip()
        return text[:_ATTACHMENT_PREVIEW_CHARS].rstrip() + "\n...[preview truncated]"

    def _format_bytes(self, size: int) -> str:
        value = float(max(0, size))
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"

    def _indent(self, text: str, prefix: str) -> str:
        return "\n".join(prefix + line for line in text.splitlines())

    def _make_progress_handler(self):
        def handler(message: str) -> None:
            self.statusChanged.emit()
        return handler
