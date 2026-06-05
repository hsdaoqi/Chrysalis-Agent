"""Review queue for long-term memory candidates."""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from chrysalis.memory.judge import PersistDecision
from chrysalis.working import WorkingMemory
from utils.text import brief_text

PENDING_STATUS = "pending"
APPROVED_STATUS = "approved"
DISCARDED_STATUS = "discarded"
MEMORY_REVIEW_STATUSES = {PENDING_STATUS, APPROVED_STATUS, DISCARDED_STATUS}
MEMORY_TARGETS = {"fact", "user_profile", "sop"}


class MemoryReviewStore:
    """Filesystem-backed queue for memory candidates awaiting user review."""

    def __init__(self, path: Path, memory_dir: Path) -> None:
        self.path = path
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def create_from_decision(
        self,
        *,
        task: str,
        result: dict[str, Any],
        decision: PersistDecision | dict[str, Any],
        working: WorkingMemory | dict[str, Any] | None = None,
        session_id: str = "",
    ) -> dict[str, Any] | None:
        data = decision.to_dict() if isinstance(decision, PersistDecision) else dict(decision)
        target = str(data.get("target") or "").strip().lower()
        if not bool(data.get("should_persist")) or target not in MEMORY_TARGETS:
            return None

        content = _candidate_content(task=task, result=result, decision=data, working=working)
        if not content:
            return None
        item_id = _item_id(target=target, task=task, content=content, session_id=session_id)
        payload = self._load()
        existing = payload["items"].get(item_id)
        if isinstance(existing, dict):
            return existing

        now = _now()
        item = {
            "id": item_id,
            "kind": "memory",
            "target": target,
            "status": PENDING_STATUS,
            "title": _candidate_title(target, task),
            "content": content,
            "reason": brief_text(str(data.get("reason") or ""), 600),
            "evidence": _string_list(data.get("evidence"), limit=8),
            "decision": data,
            "source_task": brief_text(task, 1_000),
            "final_preview": brief_text(str(result.get("final") or ""), 1_000),
            "session_id": session_id,
            "created_at": now,
            "updated_at": now,
            "approved_at": None,
            "discarded_at": None,
        }
        payload["items"][item_id] = item
        self._save(payload)
        return item

    def list_items(self, status: str | None = None) -> list[dict[str, Any]]:
        payload = self._load()
        items = [dict(item) for item in payload["items"].values() if isinstance(item, dict)]
        if status:
            items = [item for item in items if str(item.get("status") or "") == status]
        return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)

    def update_item(
        self,
        item_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        target: str | None = None,
    ) -> dict[str, Any] | None:
        payload = self._load()
        item = payload["items"].get(item_id)
        if not isinstance(item, dict):
            return None
        if title is not None:
            item["title"] = brief_text(title.strip(), 180)
        if content is not None:
            item["content"] = content.strip()
        if target is not None:
            normalized = target.strip().lower()
            if normalized in MEMORY_TARGETS:
                item["target"] = normalized
        item["updated_at"] = _now()
        payload["items"][item_id] = item
        self._save(payload)
        return dict(item)

    def approve(
        self,
        item_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        target: str | None = None,
        write_global: bool = True,
        artifact: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = self.update_item(item_id, title=title, content=content, target=target)
        if item is None:
            return {"ok": False, "error": f"memory candidate not found: {item_id}"}
        if not str(item.get("content") or "").strip():
            return {"ok": False, "error": "memory content cannot be empty"}
        now = _now()
        item["status"] = APPROVED_STATUS
        item["approved_at"] = now
        item["discarded_at"] = None
        item["updated_at"] = now
        if artifact:
            item["artifact"] = artifact
        if write_global and str(item.get("target") or "") != "sop":
            self._append_to_global_memory(item)
        payload = self._load()
        payload["items"][item_id] = item
        self._save(payload)
        return {"ok": True, "message": "memory approved", "item": dict(item)}

    def discard(self, item_id: str) -> dict[str, Any]:
        payload = self._load()
        item = payload["items"].get(item_id)
        if not isinstance(item, dict):
            return {"ok": False, "error": f"memory candidate not found: {item_id}"}
        now = _now()
        item["status"] = DISCARDED_STATUS
        item["discarded_at"] = now
        item["updated_at"] = now
        payload["items"][item_id] = item
        self._save(payload)
        return {"ok": True, "message": "memory discarded", "item": dict(item)}

    def _append_to_global_memory(self, item: dict[str, Any]) -> None:
        path = self.memory_dir / "global_mem.txt"
        now = str(item.get("approved_at") or _now())
        target = str(item.get("target") or "fact")
        title = str(item.get("title") or _target_label(target))
        reason = str(item.get("reason") or "")
        content = str(item.get("content") or "").strip()
        block = [
            "",
            f"## {title}",
            f"- target: {target}",
            f"- approved_at: {now}",
        ]
        if reason:
            block.append(f"- reason: {reason}")
        block.extend(["", content, ""])
        previous = ""
        try:
            previous = path.read_text(encoding="utf-8")
        except OSError:
            pass
        path.write_text(previous.rstrip() + "\n" + "\n".join(block), encoding="utf-8")

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, dict):
            items = {}
        return {"version": 1, "items": items}

    def _save(self, payload: dict[str, Any]) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=self.path.name, suffix=".tmp")
        try:
            with open(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
            Path(tmp).replace(self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def _candidate_content(
    *,
    task: str,
    result: dict[str, Any],
    decision: dict[str, Any],
    working: WorkingMemory | dict[str, Any] | None,
) -> str:
    if isinstance(working, WorkingMemory):
        working_data = working.snapshot()
    elif isinstance(working, dict):
        working_data = dict(working)
    else:
        working_data = {}
    requested = str(working_data.get("long_term_update_requested") or "").strip()
    if requested:
        return brief_text(requested, 1_500)
    final = str(result.get("final") or "").strip()
    reason = str(decision.get("reason") or "").strip()
    if final:
        return brief_text(final, 1_500)
    if reason:
        return brief_text(reason, 1_500)
    return brief_text(task, 1_500)


def _candidate_title(target: str, task: str) -> str:
    return f"{_target_label(target)}: {brief_text(task, 80)}"


def _target_label(target: str) -> str:
    labels = {
        "fact": "Project fact",
        "user_profile": "User preference",
        "sop": "Operating note",
    }
    return labels.get(target, "Memory")


def _item_id(*, target: str, task: str, content: str, session_id: str) -> str:
    raw = json.dumps(
        {
            "target": target,
            "task": brief_text(task, 600),
            "content": brief_text(content, 600),
            "session_id": session_id,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return "mem-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _string_list(value: Any, *, limit: int) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    elif isinstance(value, tuple):
        values = list(value)
    else:
        values = [value]
    return [brief_text(str(item), 240) for item in values if str(item).strip()][:limit]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
