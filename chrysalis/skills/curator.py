"""Skill draft generation from successful tasks.

This module deliberately stays conservative: it creates draft skills only
when the task explicitly asked for long-term learning or when the run was
large enough to suggest reusable workflow value. Drafts are not injected until
promoted to active.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chrysalis.memory import PersistDecision
from chrysalis.skills.store import ACTIVE_STATUS, DRAFT_STATUS, SkillRecord, SkillStore
from chrysalis.working import WorkingMemory
from utils.text import brief_text


@dataclass
class DraftDecision:
    should_create: bool
    reason: str


class SkillCurator:
    def __init__(
        self,
        store: SkillStore | None = None,
        *,
        min_turns: int = 16,
        min_tool_calls: int = 5,
        auto_promote: bool = True,
    ) -> None:
        self.store = store or SkillStore()
        self.min_turns = min_turns
        self.min_tool_calls = min_tool_calls
        self.auto_promote = auto_promote

    def maybe_create_draft(
        self,
        *,
        task: str,
        result: dict[str, Any],
        working: WorkingMemory,
        history_lines: list[str],
        tool_trace: list[dict[str, Any]],
        session_id: str = "",
        memory_decision: PersistDecision | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        decision = self._should_create(result, working, tool_trace, memory_decision)
        if not decision.should_create:
            payload = {"ok": False, "skipped": True, "reason": decision.reason}
            if memory_decision is not None:
                payload["memory_decision"] = _decision_dict(memory_decision)
            return payload

        category = _infer_category(tool_trace, task)
        name = _infer_name(task, category)
        tools = _tool_names(tool_trace)
        description = _description(task, result, working)
        metadata = {
            "when_to_use": _when_to_use(task, working),
            "key_steps": _key_steps(tool_trace),
            "tools": tools,
            "sop_refs": _sop_refs(working),
            "safety_notes": _safety_notes(tool_trace),
            "provenance": {
                "session_id": session_id,
                "task": brief_text(task, 400),
                "reason": decision.reason,
                "history_tail": history_lines[-12:],
            },
            "draft": True,
            "review": {
                "status": "pending",
                "created_at": _now(),
                "reason": decision.reason,
            },
        }
        if memory_decision is not None:
            metadata["memory_decision"] = _decision_dict(memory_decision)
        record = self.store.create(
            name=name,
            description=description,
            body=_render_body(
                title=name,
                description=description,
                task=task,
                result=result,
                working=working,
                tool_trace=tool_trace,
                decision=decision,
            ),
            category=category,
            tags=_tags(category, tools, task),
            status=DRAFT_STATUS,
            metadata=metadata,
        )
        self._write_trace(record, task, result, working, tool_trace)
        validation = self.validate_skill(record, result=result, working=working, tool_trace=tool_trace)
        self.store.update(record.name, metadata={"validation": validation}, include_drafts=True, include_archived=True)
        updated = self.store.find(record.name, include_drafts=True, include_archived=True) or record

        if not self.auto_promote:
            return {
                "ok": True,
                "draft_created": True,
                "review_required": True,
                "validated": bool(validation["ok"]),
                "validation": validation,
                "skill": updated.summary(),
            }

        if not validation["ok"]:
            return {
                "ok": True,
                "draft_created": True,
                "validated": False,
                "validation": validation,
                "skill": updated.summary(),
            }

        merge_target = self.find_merge_target(record)
        if merge_target is not None:
            merged = self.merge_into_active(merge_target, record, validation=validation)
            return {
                "ok": True,
                "merged": True,
                "validation": validation,
                "skill": merged.summary(),
                "merged_into": merge_target.summary(),
            }

        promoted = self.promote_validated_draft(record, validation=validation)
        return {
            "ok": True,
            "promoted": True,
            "validation": validation,
            "skill": promoted.summary(),
        }

    def validate_skill(
        self,
        record: SkillRecord,
        *,
        result: dict[str, Any] | None = None,
        working: WorkingMemory | None = None,
        tool_trace: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        tool_trace = tool_trace or []
        working = working or WorkingMemory()
        result = result or {}
        issues: list[str] = []
        sections = _section_index(record.body)
        if not record.metadata.get("description"):
            issues.append("missing description")
        if not sections.get("when_to_use"):
            issues.append("missing When To Use section")
        if not sections.get("steps"):
            issues.append("missing Steps section")
        if not sections.get("provenance"):
            issues.append("missing Provenance section")
        if any(tool in {"file_write", "file_patch"} for tool in _tool_names(tool_trace)) and not sections.get("failure_modes"):
            issues.append("missing Failure Modes section for mutating workflow")
        if not _key_steps(tool_trace):
            issues.append("could not derive key steps")

        score = 1.0
        score -= 0.2 * len(issues)
        if working.long_term_update_requested:
            score += 0.2
        if result.get("usage", {}).get("turns", 0):
            score += min(0.3, int(result["usage"].get("turns", 0)) / 40.0)
        score = max(0.0, min(1.0, score))

        return {
            "ok": not issues,
            "status": "passed" if not issues else "failed",
            "issues": issues,
            "score": round(score, 3),
            "checked_at": _now(),
            "sections": sorted(sections.keys()),
        }

    def find_merge_target(self, record: SkillRecord) -> SkillRecord | None:
        query = " ".join([
            record.metadata.get("title", ""),
            record.metadata.get("description", ""),
            " ".join(str(tag) for tag in record.metadata.get("tags", [])),
            " ".join(str(tool) for tool in record.metadata.get("tools", [])),
        ])
        candidates = [
            candidate for candidate in self.store.search(query, top_k=5, status=ACTIVE_STATUS)
            if candidate.path != record.path
        ]
        if not candidates:
            return None
        top = candidates[0]
        same_category = top.category == record.category
        if top.score < 1.8 and not same_category:
            return None
        if top.score < 1.4 and not _shared_keywords(top, record):
            return None
        return top

    def merge_into_active(
        self,
        target: SkillRecord,
        source: SkillRecord,
        *,
        validation: dict[str, Any] | None = None,
    ) -> SkillRecord:
        merged_body = _merge_skill_body(target.body, source)
        merged_meta = _merge_skill_metadata(target.metadata, source.metadata)
        merged_meta["id"] = target.id
        merged_meta["name"] = target.name
        merged_meta["title"] = target.metadata.get("title") or target.name
        merged_meta["category"] = target.category
        merged_meta["updated_at"] = _now()
        merged_meta["status"] = ACTIVE_STATUS
        merged_meta["validation"] = validation or {}
        merged_meta["merged_from"] = _merge_unique_values(merged_meta.get("merged_from"), source.id)
        updated = self.store.update(
            target.name,
            metadata=merged_meta,
            body=merged_body,
            include_drafts=True,
            include_archived=True,
        )
        if updated is None:
            raise RuntimeError(f"failed to update merge target: {target.name}")
        archived = self.store.archive(source.name)
        if not archived.get("ok"):
            raise RuntimeError(f"failed to archive merged draft: {source.name}")
        return updated

    def promote_validated_draft(
        self,
        record: SkillRecord,
        *,
        validation: dict[str, Any] | None = None,
    ) -> SkillRecord:
        self.store.update(
            record.name,
            metadata={
                "validation": validation or {},
                "promoted_at": _now(),
            },
            include_drafts=True,
            include_archived=True,
        )
        promoted = self.store.promote(record.name)
        if not promoted.get("ok"):
            raise RuntimeError(str(promoted.get("error") or "promote failed"))
        refreshed = self.store.find(record.name, include_drafts=True, include_archived=True)
        if refreshed is None:
            raise RuntimeError(f"promoted skill not found after move: {record.name}")
        return refreshed

    def _should_create(
        self,
        result: dict[str, Any],
        working: WorkingMemory,
        tool_trace: list[dict[str, Any]],
        memory_decision: PersistDecision | dict[str, Any] | None = None,
    ) -> DraftDecision:
        if not result.get("ok"):
            return DraftDecision(False, "task did not complete successfully")
        if result.get("need_user") or result.get("cancelled"):
            return DraftDecision(False, "task was interrupted or waiting for input")
        if memory_decision is not None:
            data = _decision_dict(memory_decision)
            if not bool(data.get("should_persist", False)):
                reason = data.get("reason") or "memory judge rejected persistence"
                return DraftDecision(False, f"memory judge rejected: {reason}")
            target = str(data.get("target") or "").strip().lower()
            if target not in {"skill", "sop"}:
                return DraftDecision(False, f"memory judge routed to {target or 'unknown'}, not skill")
            if str(data.get("safety_risk") or "").strip().lower() == "high":
                return DraftDecision(False, "memory judge marked high safety risk")
            return DraftDecision(True, f"memory judge approved {target}: {data.get('reason', '')}")
        if working.long_term_update_requested:
            return DraftDecision(True, f"agent requested long-term update: {working.long_term_update_requested}")

        turns = int(
            (result.get("agent_turns") or 0)
            or (result.get("turns") or 0)
            or ((result.get("usage") or {}).get("turns") or 0)
        )
        if turns >= self.min_turns:
            return DraftDecision(True, f"large successful task: {turns} turns")
        if len(tool_trace) >= self.min_tool_calls:
            return DraftDecision(True, f"multi-tool successful task: {len(tool_trace)} tool calls")
        if turns >= 2 and len(tool_trace) >= 1:
            return DraftDecision(True, f"successful task with reusable tool path: {turns} turns, {len(tool_trace)} tool calls")
        return DraftDecision(False, "task was too small to draft a reusable skill")

    def _write_trace(
        self,
        record: SkillRecord,
        task: str,
        result: dict[str, Any],
        working: WorkingMemory,
        tool_trace: list[dict[str, Any]],
    ) -> None:
        payload = {
            "task": task,
            "final": brief_text(str(result.get("final", "")), 2_000),
            "working": working.snapshot(),
            "tool_trace": tool_trace,
        }
        trace_path = record.path / "trace.json"
        trace_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

def _infer_category(tool_trace: list[dict[str, Any]], task: str) -> str:
    tools = set(_tool_names(tool_trace))
    lowered = task.lower()
    if {"web_scan", "web_execute_js"} & tools or any(word in lowered for word in ("web", "browser", "网页", "浏览器")):
        return "browser"
    if {"file_read", "file_write", "file_patch"} & tools:
        return "file"
    if "code_run" in tools or any(word in lowered for word in ("python", "script", "test", "pytest", "脚本", "测试")):
        return "code"
    if "spawn_subagent" in tools:
        return "agent"
    return "general"


def _infer_name(task: str, category: str) -> str:
    words = re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", task.lower())
    stop = {"please", "help", "with", "this", "that", "帮我", "一下", "请", "这个", "那个"}
    picked = [word for word in words if word not in stop][:6]
    if not picked:
        picked = ["workflow"]
    return f"{category}-{'-'.join(picked)}"


def _description(task: str, result: dict[str, Any], working: WorkingMemory) -> str:
    if working.long_term_update_requested:
        return working.long_term_update_requested
    final = str(result.get("final", "")).strip()
    if final:
        return f"Reusable workflow learned from: {brief_text(task, 120)}"
    return brief_text(task, 160)


def _when_to_use(task: str, working: WorkingMemory) -> list[str]:
    hints = [f"遇到类似任务时：{brief_text(task, 120)}"]
    if working.related_sop:
        hints.append(f"任务需要参考 {working.related_sop}")
        if working.plan_goal:
            hints.append(f"任务目标类似：{brief_text(working.plan_goal, 120)}")
    if working.plan_goal:
        hints.append(f"Similar plan goal: {brief_text(working.plan_goal, 120)}")
    if working.plan_summary:
        hints.append(f"Similar plan summary: {brief_text(working.plan_summary, 120)}")
    return hints


def _key_steps(tool_trace: list[dict[str, Any]]) -> list[str]:
    steps: list[str] = []
    seen: set[str] = set()
    for item in tool_trace:
        tool = str(item.get("tool", ""))
        if not tool or tool in seen:
            continue
        seen.add(tool)
        steps.append(f"Use {tool} when needed; inspect args and results before the next step.")
    return steps or ["Review the task, choose the relevant tools, verify output, then summarize the result."]


def _tool_names(tool_trace: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for item in tool_trace:
        name = str(item.get("tool", "")).strip()
        if name and name not in names:
            names.append(name)
    return names


def _sop_refs(working: WorkingMemory) -> list[str]:
    if not working.related_sop:
        return []
    return [part for part in working.related_sop.replace(",", " ").split() if part]


def _safety_notes(tool_trace: list[dict[str, Any]]) -> list[str]:
    tools = set(_tool_names(tool_trace))
    notes: list[str] = []
    if {"file_write", "file_patch"} & tools:
        notes.append("Read the target file before writing and verify diffs after mutation.")
    if "code_run" in tools:
        notes.append("Prefer bounded commands and inspect stdout/stderr before proceeding.")
    if {"web_scan", "web_execute_js"} & tools:
        notes.append("Handle login walls and private browser state with user confirmation.")
    return notes


def _tags(category: str, tools: list[str], task: str) -> list[str]:
    tags = [category]
    tags.extend(tool.replace("_", "-") for tool in tools[:4])
    if any(ch in task for ch in "测试验证检查"):
        tags.append("verify")
    return sorted(set(tags))


def _shared_keywords(a: SkillRecord, b: SkillRecord) -> bool:
    a_tokens = set(_tokenize(_skill_text(a)))
    b_tokens = set(_tokenize(_skill_text(b)))
    return len(a_tokens & b_tokens) >= 4


def _merge_skill_metadata(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    identity_keys = {"id", "name", "title", "category", "status", "created_at"}
    for key in ("tags", "tools", "sop_refs", "when_to_use", "key_steps", "safety_notes"):
        merged[key] = _merge_unique_values(merged.get(key), patch.get(key))
    provenance = dict(merged.get("provenance") or {})
    provenance.update(patch.get("provenance") or {})
    if provenance:
        merged["provenance"] = provenance
    validation = dict(merged.get("validation") or {})
    validation.update(patch.get("validation") or {})
    if validation:
        merged["validation"] = validation
    stats = dict(merged.get("stats") or {})
    stats.update(patch.get("stats") or {})
    if stats:
        merged["stats"] = stats
    for key, value in patch.items():
        if key in {"tags", "tools", "sop_refs", "when_to_use", "key_steps", "safety_notes", "provenance", "validation", "stats"}:
            continue
        if key in identity_keys:
            continue
        if key == "description" and merged.get("description"):
            continue
        if value is not None:
            merged[key] = value
    return merged


def _merge_skill_body(existing_body: str, source: SkillRecord) -> str:
    source_meta = source.metadata
    blocks = [
        "",
        "## Related Drafts",
        f"- {source_meta.get('title') or source.name}: {source_meta.get('description', '')}",
    ]
    when = source_meta.get("when_to_use") or []
    if when:
        blocks.append("  - when_to_use: " + "; ".join(str(item) for item in when[:3]))
    steps = source_meta.get("key_steps") or _extract_key_steps(source.body)
    if steps:
        blocks.append("  - key_steps: " + " | ".join(str(item) for item in steps[:5]))
    notes = source_meta.get("safety_notes") or source_meta.get("failure_modes") or []
    if notes:
        blocks.append("  - notes: " + "; ".join(str(item) for item in notes[:3]))
    merged = existing_body.rstrip()
    if "## Related Drafts" in merged:
        return merged + "\n" + "\n".join(blocks[2:]) + "\n"
    return merged + "\n" + "\n".join(blocks) + "\n"


def _merge_unique_values(existing: Any, incoming: Any) -> list[Any]:
    merged: list[Any] = []
    for value in _normalize_values(existing) + _normalize_values(incoming):
        if value not in merged:
            merged.append(value)
    return merged


def _decision_dict(decision: PersistDecision | dict[str, Any]) -> dict[str, Any]:
    if isinstance(decision, PersistDecision):
        return decision.to_dict()
    return dict(decision)


def _normalize_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _skill_text(record: SkillRecord) -> str:
    meta = record.metadata
    return " ".join([
        record.id,
        record.name,
        str(meta.get("title", "")),
        str(meta.get("description", "")),
        str(meta.get("category", "")),
        " ".join(str(tag) for tag in meta.get("tags", [])),
        " ".join(str(item) for item in meta.get("when_to_use", [])),
        " ".join(str(item) for item in meta.get("tools", [])),
        " ".join(str(item) for item in meta.get("sop_refs", [])),
        record.body,
    ])


def _tokenize(text: str) -> list[str]:
    lowered = text.lower()
    words = re.findall(r"[a-z0-9_./-]+", lowered)
    cjk = re.findall(r"[\u4e00-\u9fff]", lowered)
    grams = ["".join(cjk[i:i + 2]) for i in range(max(0, len(cjk) - 1))]
    return [token for token in words + cjk + grams if len(token) > 1 or "\u4e00" <= token <= "\u9fff"]


def _section_index(body: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for raw in body.splitlines():
        line = raw.rstrip()
        heading = re.match(r"^##\s+(.+)$", line.strip())
        if heading:
            current = _normalize_section_name(heading.group(1))
            sections.setdefault(current, [])
            continue
        if current:
            sections.setdefault(current, []).append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items() if "\n".join(lines).strip()}


def _normalize_section_name(name: str) -> str:
    value = name.strip().lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _render_body(
    *,
    title: str,
    description: str,
    task: str,
    result: dict[str, Any],
    working: WorkingMemory,
    tool_trace: list[dict[str, Any]],
    decision: DraftDecision,
) -> str:
    lines = [
        f"# {title}",
        "",
        "## About",
        description or "A lightweight SOP-style skill note captured from a successful Chrysalis task.",
        "",
        "## When To Use",
    ]
    for item in _when_to_use(task, working):
        lines.append(f"- {item}")
    lines.extend([
        "",
        "## Steps",
    ])
    for index, step in enumerate(_key_steps(tool_trace), 1):
        lines.append(f"{index}. {step}")
    lines.extend(["", "## Helper Files"])
    lines.append("- Optional `.py` helper files can live next to this `SKILL.md` when the workflow needs deterministic code.")
    lines.extend(["", "## Tools Used"])
    for name in _tool_names(tool_trace):
        lines.append(f"- {name}")
    if working.snapshot():
        lines.extend(["", "## Working Notes", json.dumps(working.snapshot(), ensure_ascii=False, indent=2)])
    if result.get("final"):
        lines.extend(["", "## Example Outcome", brief_text(str(result.get("final", "")), 1_200)])
    warnings = _safety_notes(tool_trace)
    if warnings:
        lines.extend(["", "## Failure Modes"])
        for warning in warnings:
            lines.append(f"- {warning}")
    lines.extend(["", "## Provenance", f"- source_task: {brief_text(task, 300)}", f"- draft_reason: {decision.reason}"])
    return "\n".join(lines).strip() + "\n"
