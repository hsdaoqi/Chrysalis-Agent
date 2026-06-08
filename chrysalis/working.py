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


DONE_STATUSES = {"completed", "complete", "done", "satisfied", "passed", "verified", "waived", "skipped"}


def _next_id_seed(items: list[Any], prefix: str) -> int:
    """根据已还原的 item id（形如 prefix_N）推出下一个可用的计数起点。"""
    highest = 0
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)$")
    for item in items:
        match = pattern.match(str(getattr(item, "id", "")))
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _normalize_status(value: Any, default: str = "pending") -> str:
    text = str(value or default).strip().lower().replace(" ", "_").replace("-", "_")
    if not text:
        text = default
    aliases = {
        "complete": "completed",
        "done": "completed",
        "finish": "completed",
        "finished": "completed",
        "success": "completed",
        "satisfy": "satisfied",
        "satisfied": "satisfied",
        "pass": "passed",
        "verified": "verified",
        "verify": "verified",
        "inprogress": "in_progress",
        "doing": "in_progress",
        "blocked_by_user": "blocked",
        "block": "blocked",
    }
    return aliases.get(text, text)


def _is_done_status(value: Any) -> bool:
    return _normalize_status(value, "pending") in DONE_STATUSES


@dataclass
class PlanItem:
    id: str
    title: str
    status: str = "pending"
    note: str = ""
    evidence: str = ""

    @classmethod
    def from_value(cls, value: Any, fallback_id: str, *, default_status: str = "pending") -> "PlanItem":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(id=fallback_id, title=value.strip(), status=default_status)
        if isinstance(value, dict):
            return cls(
                id=str(value.get("id") or fallback_id),
                title=str(
                    value.get("title")
                    or value.get("text")
                    or value.get("criterion")
                    or value.get("step")
                    or value.get("task")
                    or ""
                ).strip(),
                status=_normalize_status(value.get("status"), default_status),
                note=str(value.get("note") or value.get("description") or "").strip(),
                evidence=str(value.get("evidence") or "").strip(),
            )
        return cls(id=fallback_id, title=str(value).strip(), status=default_status)

    def is_done(self) -> bool:
        return _is_done_status(self.status)

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "title": self.title,
            "status": self.status,
        }
        if self.note:
            data["note"] = self.note
        if self.evidence:
            data["evidence"] = self.evidence
        return data


@dataclass
class WorkingMemory:
    key_info: str = ""
    related_sop: str = ""
    long_term_update_requested: str = ""
    todo_goal: str = ""
    todos: list[TodoItem] = field(default_factory=list)
    plan_goal: str = ""
    plan_status: str = ""
    plan_summary: str = ""
    plan_steps: list[PlanItem] = field(default_factory=list)
    plan_acceptance_criteria: list[PlanItem] = field(default_factory=list)
    plan_evidence: list[str] = field(default_factory=list)
    plan_blocker: str = ""
    rounds_since_todo: int = 0
    rounds_since_plan: int = 0
    todo_reminder_interval: int = 4
    plan_reminder_interval: int = 3
    max_key_info_chars: int = 1200
    max_related_sop_chars: int = 300
    max_reason_chars: int = 300
    max_plan_chars: int = 1600
    max_plan_evidence_chars: int = 300
    _touched: bool = field(default=False, init=False, repr=False)
    _todo_id_seed: Any = field(default_factory=lambda: count(1), init=False, repr=False)
    _plan_step_id_seed: Any = field(default_factory=lambda: count(1), init=False, repr=False)
    _plan_criterion_id_seed: Any = field(default_factory=lambda: count(1), init=False, repr=False)

    def reset(self) -> None:
        self.key_info = ""
        self.related_sop = ""
        self.long_term_update_requested = ""
        self.todo_goal = ""
        self.todos.clear()
        self._clear_plan()
        self.rounds_since_todo = 0
        self.rounds_since_plan = 0
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

    def update_plan(
        self,
        *,
        goal: str = "",
        steps: Any = None,
        acceptance_criteria: Any = None,
        evidence: Any = None,
        status: str = "",
        summary: str = "",
        blocker: str = "",
        action: str = "set",
    ) -> dict:
        action = (action or "set").strip().lower()
        if action in {"clear", "reset"}:
            self._clear_plan()
            self._touched = True
            return {"ok": True, "message": "plan cleared", "plan": self.plan_snapshot()}

        parsed_steps = self._parse_plan_items(steps, prefix="step")
        parsed_criteria = self._parse_plan_items(acceptance_criteria, prefix="criteria")
        parsed_evidence = self._parse_evidence(evidence)

        if action in {"set", "replace"}:
            self._clear_plan(keep_rounds=True)
            self._apply_plan_fields(goal=goal, status=status or "active", summary=summary, blocker=blocker)
            self.plan_steps = parsed_steps
            self.plan_acceptance_criteria = parsed_criteria
            self.plan_evidence = parsed_evidence
            self.rounds_since_plan = 0
        elif action == "append":
            self._apply_plan_fields(goal=goal, status=status, summary=summary, blocker=blocker)
            self.plan_steps.extend(parsed_steps)
            self.plan_acceptance_criteria.extend(parsed_criteria)
            self._append_plan_evidence(parsed_evidence)
        elif action == "update":
            self._apply_plan_fields(goal=goal, status=status, summary=summary, blocker=blocker)
            self.plan_steps = self._merge_plan_items(self.plan_steps, parsed_steps)
            self.plan_acceptance_criteria = self._merge_plan_items(self.plan_acceptance_criteria, parsed_criteria)
            self._append_plan_evidence(parsed_evidence)
        elif action in {"complete", "satisfy"}:
            self._apply_plan_fields(goal=goal, status=status, summary=summary, blocker=blocker)
            if parsed_steps:
                self._mark_matching_plan_items(self.plan_steps, parsed_steps, "completed")
            elif action == "complete":
                for item in self.plan_steps:
                    item.status = "completed"
            if parsed_criteria:
                self._mark_matching_plan_items(self.plan_acceptance_criteria, parsed_criteria, "satisfied")
            elif action in {"complete", "satisfy"}:
                for item in self.plan_acceptance_criteria:
                    item.status = "satisfied"
            self._append_plan_evidence(parsed_evidence)
            if not status and self._plan_all_done():
                self.plan_status = "completed"
        elif action == "block":
            self._apply_plan_fields(goal=goal, status=status or "blocked", summary=summary, blocker=blocker)
            self._append_plan_evidence(parsed_evidence)
        elif action == "status":
            self._apply_plan_fields(goal=goal, status=status, summary=summary, blocker=blocker)
            self._append_plan_evidence(parsed_evidence)
        elif action == "evidence":
            self._apply_plan_fields(goal=goal, status=status, summary=summary, blocker=blocker)
            self._append_plan_evidence(parsed_evidence)
        elif action == "reorder":
            if parsed_steps:
                self.plan_steps = parsed_steps
            if parsed_criteria:
                self.plan_acceptance_criteria = parsed_criteria
            self._apply_plan_fields(goal=goal, status=status, summary=summary, blocker=blocker)
            self._append_plan_evidence(parsed_evidence)
        else:
            return {"ok": False, "message": f"unsupported plan action: {action}"}

        if self.plan_goal or self.plan_steps or self.plan_acceptance_criteria or self.plan_evidence:
            if not self.plan_status:
                self.plan_status = "active"
        self._touched = True
        return {"ok": True, "message": "plan updated", "plan": self.plan_snapshot()}

    def tick_round(self) -> None:
        if self.todos:
            self.rounds_since_todo += 1
        if self.has_active_plan():
            self.rounds_since_plan += 1

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

    def should_remind_plan(self) -> bool:
        return self.has_active_plan() and not self._plan_all_done() and self.rounds_since_plan >= self.plan_reminder_interval

    def consume_plan_reminder(self) -> str:
        if not self.should_remind_plan():
            return ""
        self.rounds_since_plan = 0
        plan = self.plan_snapshot()
        lines = ["## Plan Reminder", "Do not final until acceptance criteria are satisfied or explicitly blocked."]
        if plan.get("goal"):
            lines.append(f"- goal: {plan['goal']}")
        for item in plan.get("steps", []):
            if not _is_done_status(item.get("status")):
                lines.append(f"- step [{item.get('status', 'pending')}]: {item.get('title', '')}")
        for item in plan.get("acceptance_criteria", []):
            if not _is_done_status(item.get("status")):
                lines.append(f"- acceptance [{item.get('status', 'pending')}]: {item.get('title', '')}")
        return "\n".join(lines)

    def has_active_plan(self) -> bool:
        return bool(self.plan_goal or self.plan_steps or self.plan_acceptance_criteria or self.plan_evidence)

    def pending_todos(self) -> list[TodoItem]:
        return [item for item in self.todos if item.status != "completed"]

    def active_todo(self) -> TodoItem | None:
        pending = self.pending_todos()
        return pending[0] if pending else None

    def active_plan_step(self) -> PlanItem | None:
        pending = [item for item in self.plan_steps if not item.is_done()]
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

    def plan_snapshot(self) -> dict:
        steps = [item.to_dict() for item in self.plan_steps]
        criteria = [item.to_dict() for item in self.plan_acceptance_criteria]
        total_steps = len(steps)
        pending_steps = len([item for item in self.plan_steps if not item.is_done()])
        total_criteria = len(criteria)
        pending_criteria = len([item for item in self.plan_acceptance_criteria if not item.is_done()])
        if not self.has_active_plan():
            return {
                "goal": "",
                "status": "",
                "summary": "",
                "blocker": "",
                "rounds_since_plan": 0,
                "plan_reminder_interval": self.plan_reminder_interval,
                "total_steps": 0,
                "pending_steps": 0,
                "completed_steps": 0,
                "total_acceptance_criteria": 0,
                "pending_acceptance_criteria": 0,
                "satisfied_acceptance_criteria": 0,
                "steps": [],
                "acceptance_criteria": [],
                "evidence": [],
            }
        return {
            "goal": self.plan_goal,
            "status": self.plan_status or "active",
            "summary": self.plan_summary,
            "blocker": self.plan_blocker,
            "rounds_since_plan": self.rounds_since_plan,
            "plan_reminder_interval": self.plan_reminder_interval,
            "total_steps": total_steps,
            "pending_steps": pending_steps,
            "completed_steps": total_steps - pending_steps,
            "total_acceptance_criteria": total_criteria,
            "pending_acceptance_criteria": pending_criteria,
            "satisfied_acceptance_criteria": total_criteria - pending_criteria,
            "steps": steps,
            "acceptance_criteria": criteria,
            "evidence": list(self.plan_evidence),
            "active_step_id": self.active_plan_step().id if self.active_plan_step() else "",
            "active_step_title": self.active_plan_step().title if self.active_plan_step() else "",
        }

    def state_snapshot(self) -> dict:
        todo = self.todo_snapshot()
        plan = self.plan_snapshot()
        data = {
            **todo,
            "todo": todo,
            "plan": plan,
            "plan_goal": plan.get("goal", ""),
            "plan_status": plan.get("status", ""),
            "plan_summary": plan.get("summary", ""),
            "plan_blocker": plan.get("blocker", ""),
            "plan_steps": plan.get("steps", []),
            "plan_acceptance_criteria": plan.get("acceptance_criteria", []),
            "plan_evidence": plan.get("evidence", []),
            "rounds_since_plan": plan.get("rounds_since_plan", 0),
            "plan_reminder_interval": plan.get("plan_reminder_interval", self.plan_reminder_interval),
            "plan_total_steps": plan.get("total_steps", 0),
            "plan_pending_steps": plan.get("pending_steps", 0),
            "plan_completed_steps": plan.get("completed_steps", 0),
            "plan_total_acceptance_criteria": plan.get("total_acceptance_criteria", 0),
            "plan_pending_acceptance_criteria": plan.get("pending_acceptance_criteria", 0),
            "plan_satisfied_acceptance_criteria": plan.get("satisfied_acceptance_criteria", 0),
            "plan_active_step_id": plan.get("active_step_id", ""),
            "plan_active_step_title": plan.get("active_step_title", ""),
        }
        if self.key_info:
            data["key_info"] = self.key_info
        if self.related_sop:
            data["related_sop"] = self.related_sop
        if self.long_term_update_requested:
            data["long_term_update_requested"] = self.long_term_update_requested
        return data

    def _clear_plan(self, *, keep_rounds: bool = False) -> None:
        self.plan_goal = ""
        self.plan_status = ""
        self.plan_summary = ""
        self.plan_steps.clear()
        self.plan_acceptance_criteria.clear()
        self.plan_evidence.clear()
        self.plan_blocker = ""
        if not keep_rounds:
            self.rounds_since_plan = 0

    def _apply_plan_fields(self, *, goal: str = "", status: str = "", summary: str = "", blocker: str = "") -> None:
        goal = goal.strip()
        status = status.strip()
        summary = summary.strip()
        blocker = blocker.strip()
        if goal:
            self.plan_goal = goal[: self.max_key_info_chars]
        if status:
            self.plan_status = _normalize_status(status, self.plan_status or "active")
        if summary:
            self.plan_summary = summary[: self.max_plan_chars]
        if blocker:
            self.plan_blocker = blocker[: self.max_plan_chars]

    def _parse_plan_items(self, values: Any, *, prefix: str) -> list[PlanItem]:
        normalized = self._normalize_plan_values(values)
        items: list[PlanItem] = []
        seed = self._plan_step_id_seed if prefix == "step" else self._plan_criterion_id_seed
        default_status = "pending"
        for value in normalized:
            item = PlanItem.from_value(value, f"{prefix}_{next(seed)}", default_status=default_status)
            if item.title:
                items.append(item)
        return items

    def _normalize_plan_values(self, values: Any) -> list[Any]:
        if values is None:
            return []
        if isinstance(values, str):
            text = values.strip()
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
            lines = [self._clean_plan_line(line) for line in text.splitlines()]
            lines = [line for line in lines if line]
            return lines or [text]
        if isinstance(values, dict):
            return [values]
        if isinstance(values, list):
            return values
        if isinstance(values, tuple):
            return list(values)
        return [values]

    def _clean_plan_line(self, line: str) -> str:
        line = line.strip()
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        line = re.sub(r"^\[[ xX-]\]\s+", "", line)
        return line.strip()

    def _parse_evidence(self, values: Any) -> list[str]:
        normalized = self._normalize_plan_values(values)
        evidence: list[str] = []
        for value in normalized:
            text = str(value).strip()
            if text:
                evidence.append(text[: self.max_plan_evidence_chars])
        return evidence

    def _append_plan_evidence(self, evidence: list[str]) -> None:
        for entry in evidence:
            if entry and entry not in self.plan_evidence:
                self.plan_evidence.append(entry[: self.max_plan_evidence_chars])

    def _merge_plan_items(self, current: list[PlanItem], updates: list[PlanItem]) -> list[PlanItem]:
        by_id = {item.id: item for item in current}
        ordered: list[PlanItem] = []
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
            if update.evidence:
                existing.evidence = update.evidence
            ordered.append(existing)
        seen = {item.id for item in ordered}
        for item in current:
            if item.id not in seen:
                ordered.append(item)
        return ordered

    def _mark_matching_plan_items(self, current: list[PlanItem], updates: list[PlanItem], status: str) -> None:
        update_ids = {item.id for item in updates if item.id}
        update_titles = {item.title for item in updates if item.title}
        for item in current:
            if item.id in update_ids or item.title in update_titles:
                item.status = status
                if updates:
                    matched = next((candidate for candidate in updates if candidate.id == item.id or candidate.title == item.title), None)
                    if matched and matched.evidence:
                        item.evidence = matched.evidence

    def _plan_all_done(self) -> bool:
        steps_done = all(item.is_done() for item in self.plan_steps) if self.plan_steps else True
        criteria_done = all(item.is_done() for item in self.plan_acceptance_criteria) if self.plan_acceptance_criteria else False
        if not self.plan_steps and not self.plan_acceptance_criteria:
            return False
        return steps_done and criteria_done

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
        if self.has_active_plan():
            plan = self.plan_snapshot()
            if plan.get("goal"):
                data["plan_goal"] = plan.get("goal", "")
            if plan.get("status"):
                data["plan_status"] = plan.get("status", "")
            if plan.get("summary"):
                data["plan_summary"] = plan.get("summary", "")
            if plan.get("blocker"):
                data["plan_blocker"] = plan.get("blocker", "")
            if plan.get("steps"):
                data["plan_steps"] = plan.get("steps", [])
            if plan.get("acceptance_criteria"):
                data["plan_acceptance_criteria"] = plan.get("acceptance_criteria", [])
            if plan.get("evidence"):
                data["plan_evidence"] = plan.get("evidence", [])
            if self.rounds_since_plan:
                data["rounds_since_plan"] = self.rounds_since_plan
        return data

    def to_dict(self) -> dict:
        """完整序列化为可 JSON 化的 dict，供 checkpoint 落盘。

        与 snapshot()/state_snapshot() 不同：这里**无损**保存全部字段（含为空的、
        round 计数、各 reminder interval），用于 restore() 精确还原中断时的工作记忆。
        """
        return {
            "key_info": self.key_info,
            "related_sop": self.related_sop,
            "long_term_update_requested": self.long_term_update_requested,
            "todo_goal": self.todo_goal,
            "todos": [
                {"id": item.id, "title": item.title, "status": item.status, "note": item.note}
                for item in self.todos
            ],
            "plan_goal": self.plan_goal,
            "plan_status": self.plan_status,
            "plan_summary": self.plan_summary,
            "plan_steps": [item.to_dict() for item in self.plan_steps],
            "plan_acceptance_criteria": [item.to_dict() for item in self.plan_acceptance_criteria],
            "plan_evidence": list(self.plan_evidence),
            "plan_blocker": self.plan_blocker,
            "rounds_since_todo": self.rounds_since_todo,
            "rounds_since_plan": self.rounds_since_plan,
            "touched": self._touched,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkingMemory":
        memory = cls()
        memory.restore(data)
        return memory

    def restore(self, data: dict) -> None:
        """从 to_dict() 的结果完整还原工作记忆（就地修改 self）。"""
        if not isinstance(data, dict):
            return
        self.key_info = str(data.get("key_info") or "")
        self.related_sop = str(data.get("related_sop") or "")
        self.long_term_update_requested = str(data.get("long_term_update_requested") or "")
        self.todo_goal = str(data.get("todo_goal") or "")
        self.todos = [
            TodoItem.from_value(value, f"todo_{index + 1}")
            for index, value in enumerate(data.get("todos") or [])
        ]
        self.plan_goal = str(data.get("plan_goal") or "")
        self.plan_status = str(data.get("plan_status") or "")
        self.plan_summary = str(data.get("plan_summary") or "")
        self.plan_steps = [
            PlanItem.from_value(value, f"step_{index + 1}")
            for index, value in enumerate(data.get("plan_steps") or [])
        ]
        self.plan_acceptance_criteria = [
            PlanItem.from_value(value, f"criteria_{index + 1}")
            for index, value in enumerate(data.get("plan_acceptance_criteria") or [])
        ]
        self.plan_evidence = [str(item) for item in (data.get("plan_evidence") or []) if str(item).strip()]
        self.plan_blocker = str(data.get("plan_blocker") or "")
        self.rounds_since_todo = int(data.get("rounds_since_todo") or 0)
        self.rounds_since_plan = int(data.get("rounds_since_plan") or 0)
        self._touched = bool(data.get("touched", False))
        # id 种子续接到已有 item 之后，避免新建 item 与还原的 item 撞 id
        self._todo_id_seed = count(_next_id_seed(self.todos, "todo"))
        self._plan_step_id_seed = count(_next_id_seed(self.plan_steps, "step"))
        self._plan_criterion_id_seed = count(_next_id_seed(self.plan_acceptance_criteria, "criteria"))

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
            if key in {"plan_steps", "plan_acceptance_criteria"} and isinstance(value, list):
                label = "plan steps" if key == "plan_steps" else "plan acceptance criteria"
                lines.append(f"- {label}:")
                for item in value:
                    note = str(item.get("note") or "").strip()
                    evidence = str(item.get("evidence") or "").strip()
                    suffix = f" ({note})" if note else ""
                    if evidence:
                        suffix += f" evidence={evidence}"
                    lines.append(f"  - [{item.get('status', 'pending')}] {item.get('title', '')}{suffix}")
                continue
            if key == "plan_evidence" and isinstance(value, list):
                lines.append("- plan evidence:")
                for item in value:
                    lines.append(f"  - {item}")
                continue
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    def todo_reminder_prompt(self) -> str:
        return self.consume_todo_reminder()

    def plan_reminder_prompt(self) -> str:
        return self.consume_plan_reminder()

    def append_to_prompt(self, prompt: str) -> str:
        working_prompt = self.to_prompt()
        if not working_prompt:
            return prompt
        return f"{prompt}\n\n{working_prompt}"
