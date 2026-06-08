"""ReviewMixin：拆分自 electron_runtime.py（方法体逐字符保留）。"""
from __future__ import annotations

from chrysalis.electron_runtime._common import *  # noqa: F401,F403


class ReviewMixin:
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

