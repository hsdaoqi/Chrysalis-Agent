"""Task-scoped working memory for iterative execution and planning."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from itertools import count
from typing import Any


@dataclass
class TodoItem:
    id: str
    title: str
    status: str = "pending"
    note: str = ""

    @classmethod
    def from_value(cls, value: Any, fallback_id: str) -> "TodoItem":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(id=fallback_id, title=value.strip())
        if isinstance(value, dict):
            return cls(
                id=str(value.get("id") or fallback_id),
                title=str(value.get("title") or value.get("text") or value.get("task") or "").strip(),
                status=str(value.get("status") or "pending").strip() or "pending",
                note=str(value.get("note") or value.get("description") or "").strip(),
            )
        return cls(id=fallback_id, title=str(value).strip())


@dataclass
class WorkingMemory:
    key_info: str = ""
    related_sop: str = ""
    long_term_update_requested: str = ""
    todo_goal: str = ""
    todos: list[TodoItem] = field(default_factory=list)
    rounds_since_todo: int = 0
    todo_reminder_interval: int = 4
    max_key_info_chars: int = 1200
    max_related_sop_chars: int = 300
    max_reason_chars: int = 300
    _touched: bool = field(default=False, init=False, repr=False)
    _todo_id_seed: Any = field(default_factory=lambda: count(1), init=False, repr=False)

    def reset(self) -> None:
        self.key_info = ""
        self.related_sop = ""
        self.long_term_update_requested = ""
        self.todo_goal = ""
        self.todos.clear()
        self.rounds_since_todo = 0
        self._touched = False

    def update_checkpoint(self, key_info: str = "", related_sop: str = "") -> dict:
        key_info = key_info.strip()
        related_sop = related_sop.strip()
        if key_info:
            self.key_info = key_info[: self.max_key_info_chars]
            self._touched = True
        if related_sop:
            self.related_sop = related_sop[: self.max_related_sop_chars]
            self._touched = True
        return {"ok": True, "message": "working checkpoint updated", "working_checkpoint": self.snapshot()}

    def request_long_term_update(self, reason: str = "") -> dict:
        reason = reason.strip() or "current task has reusable experience"
        self.long_term_update_requested = reason[: self.max_reason_chars]
        self._touched = True
        return {
            "ok": True,
            "message": "long-term memory update requested",
            "reason": self.long_term_update_requested,
        }

    def update_todos(self, todos: list[Any] | None = None, *, goal: str = "", action: str = "set") -> dict:
        goal = goal.strip()
        action = (action or "set").strip().lower()
        if goal:
            self.todo_goal = goal[: self.max_key_info_chars]

        items = self._parse_todos(todos or [])
        if action in {"set", "replace"}:
            self.todos = items
            self.rounds_since_todo = 0
        elif action == "append":
            self.todos.extend(items)
        elif action == "update":
            self.todos = self._merge_todos(self.todos, items)
        elif action == "complete":
            completed_ids = {item.id for item in items if item.id}
            completed_titles = {item.title for item in items if item.title}
            for item in self.todos:
                if item.id in completed_ids or item.title in completed_titles:
                    item.status = "completed"
        elif action in {"clear", "reset"}:
            self.todos.clear()
            self.rounds_since_todo = 0
        elif action == "reorder":
            self.todos = items
        else:
            return {"ok": False, "message": f"unsupported todo action: {action}"}

        if not self.todos:
            self.todo_goal = ""
            self.rounds_since_todo = 0

        self._move_completed_to_bottom()
        self._touched = True
        return {"ok": True, "message": "TODO list updated", "todo_state": self.todo_snapshot()}

    def tick_round(self) -> None:
        if self.todos:
            self.rounds_since_todo += 1

    def should_remind_todo(self) -> bool:
        return bool(self.todos) and any(item.status != "completed" for item in self.todos) and self.rounds_since_todo >= self.todo_reminder_interval

    def consume_todo_reminder(self) -> str:
        if not self.should_remind_todo():
            return ""
        self.rounds_since_todo = 0
        pending = self.pending_todos()
        lines = ["## TODO Reminder", "Plan first, then execute."]
        if self.todo_goal:
            lines.append(f"- goal: {self.todo_goal}")
        for item in pending:
            suffix = f" ({item.note})" if item.note else ""
            lines.append(f"- [{item.status}] {item.title}{suffix}")
        return "\n".join(lines)

    def pending_todos(self) -> list[TodoItem]:
        return [item for item in self.todos if item.status != "completed"]

    def active_todo(self) -> TodoItem | None:
        pending = self.pending_todos()
        return pending[0] if pending else None

    def todo_snapshot(self) -> dict:
        pending = self.pending_todos()
        active = pending[0] if pending else None
        total = len(self.todos)
        pending_count = len(pending)
        if not total:
            return {
                "goal": "",
                "rounds_since_todo": 0,
                "todo_reminder_interval": self.todo_reminder_interval,
                "total_count": 0,
                "pending_count": 0,
                "completed_count": 0,
                "todos": [],
            }
        data: dict[str, Any] = {
            "goal": self.todo_goal,
            "rounds_since_todo": self.rounds_since_todo,
            "todo_reminder_interval": self.todo_reminder_interval,
            "total_count": total,
            "pending_count": pending_count,
            "completed_count": total - pending_count,
            "todos": [
                {"id": item.id, "title": item.title, "status": item.status, "note": item.note}
                for item in self.todos
            ],
        }
        if active is not None:
            data["active_todo_id"] = active.id
            data["active_todo_title"] = active.title
        return data

    def _parse_todos(self, todos: Any) -> list[TodoItem]:
        todos = self._normalize_todo_values(todos)
        parsed: list[TodoItem] = []
        for value in todos:
            item = TodoItem.from_value(value, f"todo_{next(self._todo_id_seed)}")
            if item.title:
                parsed.append(item)
        return parsed

    def _normalize_todo_values(self, todos: Any) -> list[Any]:
        if todos is None:
            return []
        if isinstance(todos, str):
            text = todos.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
            lines = [self._clean_todo_line(line) for line in text.splitlines()]
            lines = [line for line in lines if line]
            return lines or [text]
        if isinstance(todos, dict):
            return [todos]
        if isinstance(todos, list):
            return todos
        if isinstance(todos, tuple):
            return list(todos)
        return [todos]

    def _clean_todo_line(self, line: str) -> str:
        line = line.strip()
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        line = re.sub(r"^\[[ xX-]\]\s+", "", line)
        return line.strip()

    def _merge_todos(self, current: list[TodoItem], updates: list[TodoItem]) -> list[TodoItem]:
        by_id = {item.id: item for item in current}
        ordered: list[TodoItem] = []
        for update in updates:
            existing = by_id.get(update.id)
            if existing is None and update.title:
                existing = next((item for item in current if item.title == update.title), None)
            if existing is None:
                ordered.append(update)
                continue
            if update.title:
                existing.title = update.title
            if update.status:
                existing.status = update.status
            if update.note:
                existing.note = update.note
            ordered.append(existing)
        seen = {item.id for item in ordered}
        for item in current:
            if item.id not in seen:
                ordered.append(item)
        return ordered

    def _move_completed_to_bottom(self) -> None:
        if not self.todos:
            return
        pending = [item for item in self.todos if item.status != "completed"]
        completed = [item for item in self.todos if item.status == "completed"]
        self.todos = pending + completed

    def snapshot(self) -> dict:
        data = {}
        if self.key_info:
            data["key_info"] = self.key_info
        if self.related_sop:
            data["related_sop"] = self.related_sop
        if self.long_term_update_requested:
            data["long_term_update_requested"] = self.long_term_update_requested
        if self.todo_goal:
            data["todo_goal"] = self.todo_goal
        if self.todos:
            data["todos"] = self.todo_snapshot().get("todos", [])
        if self.rounds_since_todo:
            data["rounds_since_todo"] = self.rounds_since_todo
        return data

    def to_prompt(self) -> str:
        snapshot = self.snapshot()
        if not snapshot:
            return ""
        lines = ["## 当前短期工作记忆"]
        for key, value in snapshot.items():
            if key == "todos" and isinstance(value, list):
                lines.append("- todos:")
                for item in value:
                    suffix = f" ({item.get('note', '')})" if item.get("note") else ""
                    lines.append(f"  - [{item.get('status', 'pending')}] {item.get('title', '')}{suffix}")
                continue
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    def todo_reminder_prompt(self) -> str:
        return self.consume_todo_reminder()

    def append_to_prompt(self, prompt: str) -> str:
        working_prompt = self.to_prompt()
        if not working_prompt:
            return prompt
        return f"{prompt}\n\n{working_prompt}"
