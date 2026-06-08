"""共享 imports / 常量 / dataclass / helper（拆分自 electron_runtime.py，逐字符保留）。"""

from __future__ import annotations

import copy
import difflib
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from chrysalis.cron.jobs import (
    CronError,
    create_job,
    list_jobs,
    load_job,
    mark_job_run,
    mark_job_started,
    pause_job,
    remove_job,
    resume_job,
    save_job_output,
    update_job,
)
from chrysalis.cron.scheduler import run_job, tick
from chrysalis.desktop_trace import TraceArchive
from chrysalis.gateway.bootstrap import (
    dependency_install_hint,
    ensure_gateway_dirs,
    gateway_process_argv,
    gateway_process_command,
    missing_gateway_dependencies,
)
from chrysalis.gateway.activity import GatewayActivityStore
from chrysalis.history_display import is_orphaned_tool_result_text
from chrysalis.kernel import Kernel, format_context_usage
from chrysalis.llm.types import Usage
from chrysalis.llm.usage import _fmt_elapsed
from chrysalis.memory import MemoryReviewStore
from chrysalis.skills.curator import SkillCurator
from chrysalis.skills.store import ACTIVE_STATUS, ARCHIVED_STATUS, DRAFT_STATUS, STALE_STATUS, SkillStore
from configs.config import PROJECT_ROOT

_FILE_MODIFY_TOOLS = {"file_write", "file_patch"}
_MAX_ATTACHMENTS = 8
_ATTACHMENT_PREVIEW_CHARS = 8_000
_WORKSPACE_PREVIEW_CHARS = 16_000
_WORKSPACE_RECENT_LIMIT = 12
_WORKSPACE_DIFF_MAX_FILE_BYTES = 1_000_000
_WORKSPACE_DIFF_MAX_TOTAL_BYTES = 24_000_000
_WORKSPACE_DIFF_MAX_FILES = 2_000
_IGNORED_WORKSPACE_DIRS = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".pytest_cache_local",
    ".ruff_cache",
    ".tmp",
    ".tox",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    ".venv",
    "venv",
}
_TEXT_EXTENSIONS = {
    ".bat",
    ".c",
    ".cfg",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".diff",
    ".env",
    ".go",
    ".h",
    ".hpp",
    ".htm",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".lua",
    ".md",
    ".patch",
    ".php",
    ".ps1",
    ".py",
    ".qml",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
_DESKTOP_GATEWAY_PLATFORMS = ("qq", "qq_personal", "wechat", "feishu")
_GATEWAY_ACTIVITY_POLL_SECONDS = 1.0
_NAPCAT_QQ_PERSONAL_ACCOUNT = os.getenv("CHRYSALIS_NAPCAT_QQ", "3843511481").strip()
_NAPCAT_ONEBOT_PORT = int(os.getenv("CHRYSALIS_NAPCAT_ONEBOT_PORT", "3001") or "3001")
_NAPCAT_WEBUI_PORT = int(os.getenv("CHRYSALIS_NAPCAT_WEBUI_PORT", "6099") or "6099")
_NAPCAT_DEFAULT_BOOTMAIN = PROJECT_ROOT / "data" / "napcat" / "OneKey_20260602_123844" / "bootmain"
_NAPCAT_LAUNCHER = Path(os.getenv("CHRYSALIS_NAPCAT_LAUNCHER", str(_NAPCAT_DEFAULT_BOOTMAIN / "launcher.bat"))).expanduser()
_DESKTOP_GATEWAY_LABELS = {
    "qq": "QQ",
    "qq_personal": "个人 QQ",
    "wechat": "微信",
    "feishu": "飞书",
}
@dataclass
class _RunningTask:
    session_id: str
    task_id: str
    kernel: Kernel
    thread: threading.Thread
    file_before: dict[str, str]
    workspace_before: dict[str, str]
    emitted_diffs: dict[str, str]
    tool_turn: int = 0
    trace_seq: int = 0


@dataclass
class _GatewayProcess:
    platform: str
    launch_platform: str
    process: subprocess.Popen
    log_file: Path
    started_at: str
    command: str
    last_error: str = ""
    return_code: int | None = None

def _configure_stdio() -> None:
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")
        except Exception:
            continue


_configure_stdio()

def _task_review_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Summarize newly-created review candidates for the desktop event stream."""

    items: list[dict[str, Any]] = []
    memory_item = result.get("memory_review_item")
    if isinstance(memory_item, dict):
        raw_id = str(memory_item.get("id") or "")
        target = str(memory_item.get("target") or "fact")
        items.append({
            "id": f"memory:{raw_id}" if raw_id else "",
            "kind": "memory",
            "title": str(memory_item.get("title") or _memory_target_label_zh(target)),
            "target": target,
            "label": _memory_target_label_zh(target),
        })

    skill = result.get("skill_draft") or result.get("skill_artifact")
    if isinstance(skill, dict):
        raw_id = str(skill.get("name") or skill.get("id") or "")
        if raw_id:
            items.append({
                "id": f"skill:{raw_id}",
                "kind": "skill",
                "title": str(skill.get("title") or raw_id),
                "target": str(skill.get("category") or ""),
                "label": "技能笔记草稿",
            })

    count = len([item for item in items if item.get("id")])
    if count <= 0:
        decision = result.get("memory_decision") if isinstance(result.get("memory_decision"), dict) else {}
        return {
            "has_candidates": False,
            "count": 0,
            "items": [],
            "headline": "",
            "reason": str(decision.get("reason") or ""),
        }
    return {
        "has_candidates": True,
        "count": count,
        "items": items,
        "headline": f"发现 {count} 个可审核的成长候选",
        "reason": "任务结束后生成了可沉淀的记忆或技能笔记草稿。",
    }


def _memory_review_summary(
    item: dict[str, Any],
    *,
    target: str,
    decision: dict[str, Any],
    evidence: list[str],
) -> dict[str, Any]:
    label = _memory_target_label_zh(target)
    reason = str(item.get("reason") or decision.get("reason") or "").strip()
    final_preview = str(item.get("final_preview") or "").strip()
    source_task = str(item.get("source_task") or "").strip()
    value_score = _optional_score(decision.get("value_score"))
    confidence = _optional_score(decision.get("confidence"))
    quality_parts = [
        f"value {value_score}" if value_score else "",
        f"confidence {confidence}" if confidence else "",
        f"stability {decision.get('stability')}" if decision.get("stability") else "",
        f"reuse {decision.get('reuse_likelihood')}" if decision.get("reuse_likelihood") else "",
    ]
    quality = " · ".join(part for part in quality_parts if part)
    if str(target or "").strip().lower() == "sop":
        title = _skill_note_title(
            str(item.get("title") or ""),
            source_task=source_task,
            fallback=str(item.get("id") or "skill-note"),
        )
        name = _skill_note_slug(title)
        return {
            "why": reason or (evidence[0] if evidence else "这条内容像可复用的操作流程，适合沉淀成技能笔记。"),
            "save_as": f"技能笔记 skills/{name}/SKILL.md；批准后不会写入 memory/global_mem.txt。",
            "reuse": _memory_reuse_text(target, source_task=source_task, final_preview=final_preview),
            "risk": _risk_text(decision.get("safety_risk"), quality=quality),
            "quality": quality,
            "next_action": "编辑适用场景和步骤后批准，或丢弃这个候选。",
        }
    return {
        "why": reason or (evidence[0] if evidence else "这条内容看起来稳定、可复用，适合进入长期记忆审核。"),
        "save_as": f"{label}，批准后会写入 memory/global_mem.txt。",
        "reuse": _memory_reuse_text(target, source_task=source_task, final_preview=final_preview),
        "risk": _risk_text(decision.get("safety_risk"), quality=quality),
        "quality": quality,
        "next_action": "编辑内容后批准，或丢弃这个候选。",
    }


def _skill_review_summary(
    record: Any,
    *,
    meta: dict[str, Any],
    review: dict[str, Any],
    provenance: dict[str, Any],
    decision: dict[str, Any],
    validation: dict[str, Any],
    evidence: list[str],
) -> dict[str, Any]:
    name = str(getattr(record, "name", "") or meta.get("name") or meta.get("id") or "skill")
    category = str(getattr(record, "category", "") or meta.get("category") or "general")
    reason = (
        str(review.get("reason") or "").strip()
        or str(provenance.get("reason") or "").strip()
        or str(decision.get("reason") or "").strip()
        or (evidence[0] if evidence else "")
    )
    tools = _review_string_list(meta.get("tools"), limit=8)
    when_to_use = _review_string_list(meta.get("when_to_use"), limit=3)
    key_steps = _review_string_list(meta.get("key_steps"), limit=4)
    validation_status = str(validation.get("status") or "unchecked")
    issues = _review_string_list(validation.get("issues"), limit=4)
    reuse = (
        "；".join(when_to_use)
        or f"后续任务命中 {category} 类别、标签或工具轨迹时，会作为相关技能笔记注入上下文。"
    )
    if key_steps:
        reuse = f"{reuse} 关键步骤：{' / '.join(key_steps)}"
    risk = (
        f"校验 {validation_status}；需处理：{'；'.join(issues)}"
        if issues
        else f"校验 {validation_status}；批准后会成为 active 技能笔记，并可在相关任务中被召回。"
    )
    return {
        "why": reason or "这次任务形成了可复用的工具路径，适合转成技能笔记草稿审核。",
        "save_as": f"技能笔记 skills/{name}/SKILL.md；类别 {category} 只作为检索标签保留。",
        "reuse": reuse,
        "risk": risk,
        "quality": f"validation {validation_status}",
        "next_action": "确认步骤和适用场景后批准，或归档这个技能笔记草稿。",
        "tools": tools,
    }


def _memory_target_label_zh(target: str) -> str:
    labels = {
        "fact": "项目事实",
        "user_profile": "用户偏好",
        "sop": "SOP/技能笔记",
    }
    return labels.get(str(target or "").strip().lower(), "长期记忆")


def _optional_score(value: Any) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{score:.2f}"


def _memory_reuse_text(target: str, *, source_task: str, final_preview: str) -> str:
    normalized = str(target or "").strip().lower()
    if normalized == "user_profile":
        return "后续任务会把它作为用户偏好注入上下文，帮助回答更贴合你的习惯。"
    if normalized == "sop":
        return "后续任务命中相关操作场景时，会作为技能笔记随上下文召回。"
    hint = source_task or final_preview
    if hint:
        return f"后续任务与这条事实相关时，会作为稳定项目记忆召回。来源线索：{hint[:180]}"
    return "后续任务与这条事实相关时，会作为稳定项目记忆召回。"


def _risk_text(safety_risk: Any, *, quality: str = "") -> str:
    risk = str(safety_risk or "").strip().lower()
    label = {
        "low": "低风险",
        "medium": "中等风险",
        "high": "高风险",
    }.get(risk, "未标记风险")
    return f"{label}{f'；{quality}' if quality else ''}"


def _skill_note_title(raw: str, *, source_task: str = "", fallback: str = "") -> str:
    text = str(raw or source_task or fallback or "skill note").strip()
    text = re.sub(
        r"^(project fact|user preference|operating note|项目事实|用户偏好|操作笔记|sop/技能笔记)\s*[:：]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+", " ", text).strip()
    return _clip_review_text(text, 120) or "skill note"


def _skill_note_slug(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    return text or "skill-note"


def _unique_skill_note_name(store: SkillStore, title: str, item_id: str) -> str:
    base = _skill_note_slug(title)[:80].strip("-_") or "skill-note"
    if store.find(base, include_drafts=True, include_archived=True) is None:
        return base
    suffix_source = _skill_note_slug(item_id).replace("mem-", "") or datetime.now().strftime("%H%M%S")
    suffix = suffix_source[-8:]
    candidate = f"{base}-{suffix}"
    if store.find(candidate, include_drafts=True, include_archived=True) is None:
        return candidate
    for index in range(2, 100):
        candidate = f"{base}-{suffix}-{index}"
        if store.find(candidate, include_drafts=True, include_archived=True) is None:
            return candidate
    return f"{base}-{suffix}-{datetime.now():%H%M%S}"


def _skill_note_description(item: dict[str, Any]) -> str:
    for value in (
        item.get("reason"),
        item.get("content"),
        item.get("source_task"),
        item.get("final_preview"),
    ):
        text = _clip_review_text(str(value or ""), 260)
        if text:
            return text
    return "Reusable SOP-style skill note captured from a reviewed memory candidate."


def _skill_note_when_to_use(item: dict[str, Any]) -> list[str]:
    values = [
        str(item.get("source_task") or "").strip(),
        str(item.get("reason") or "").strip(),
        str(item.get("title") or "").strip(),
    ]
    result: list[str] = []
    for value in values:
        text = _clip_review_text(value, 180)
        if text and text not in result:
            result.append(text)
    return result or ["Use when a task matches this reviewed SOP or recurring workflow."]


def _memory_skill_steps(content: str, *, limit: int = 6) -> list[str]:
    steps: list[str] = []
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"^\d+[.)、]\s*", "", line)
        line = _clip_review_text(line, 180)
        if line and line not in steps:
            steps.append(line)
        if len(steps) >= limit:
            break
    if steps:
        return steps
    fallback = _clip_review_text(str(content or ""), 180)
    return [fallback] if fallback else ["Follow the reviewed SOP note."]


def _render_memory_skill_note(
    *,
    title: str,
    description: str,
    content: str,
    source_task: str,
    item_id: str,
    reason: str,
    evidence: list[str],
) -> str:
    lines = [
        f"# {title}",
        "",
        "## About",
        description or "Reusable SOP-style skill note captured from a reviewed memory candidate.",
        "",
        "## When To Use",
    ]
    when = [source_task, reason]
    for item in when:
        text = _clip_review_text(item, 220)
        if text:
            lines.append(f"- {text}")
    if lines[-1] == "## When To Use":
        lines.append("- Use when a task matches this reviewed SOP or recurring workflow.")
    lines.extend(["", "## Steps"])
    for index, step in enumerate(_memory_skill_steps(content), 1):
        lines.append(f"{index}. {step}")
    lines.extend([
        "",
        "## Notes",
        content.strip(),
        "",
        "## Helper Files",
        "- Optional `.py` helper files can live next to this `SKILL.md` when the workflow needs deterministic code.",
        "",
        "## Provenance",
        f"- review_item: {item_id}",
    ])
    if source_task:
        lines.append(f"- source_task: {_clip_review_text(source_task, 300)}")
    if reason:
        lines.append(f"- reason: {_clip_review_text(reason, 300)}")
    for item in evidence[:5]:
        lines.append(f"- evidence: {_clip_review_text(item, 240)}")
    return "\n".join(lines).strip() + "\n"


def _clip_review_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _memory_review_payload(item: dict[str, Any]) -> dict[str, Any]:
    raw_id = str(item.get("id") or "")
    target = str(item.get("target") or "fact")
    content = str(item.get("content") or "")
    decision = copy.deepcopy(item.get("decision")) if isinstance(item.get("decision"), dict) else {}
    evidence = _review_string_list(item.get("evidence"), limit=12)
    summary = _memory_review_summary(item, target=target, decision=decision, evidence=evidence)
    artifact = copy.deepcopy(item.get("artifact")) if isinstance(item.get("artifact"), dict) else {}
    artifact_path = str(artifact.get("path") or "") if artifact else ""
    if artifact:
        summary = dict(summary)
        skill_name = str(artifact.get("name") or artifact.get("id") or "")
        if artifact_path:
            summary["save_as"] = f"技能笔记 {Path(artifact_path) / 'SKILL.md'}"
        elif skill_name:
            summary["save_as"] = f"技能笔记 skills/{skill_name}/SKILL.md"
    return {
        "id": f"memory:{raw_id}",
        "raw_id": raw_id,
        "kind": "memory",
        "status": _review_status(str(item.get("status") or "pending")),
        "target": target,
        "title": str(item.get("title") or _memory_target_label(target)),
        "description": str(item.get("final_preview") or item.get("source_task") or ""),
        "content": content,
        "body": content,
        "reason": str(item.get("reason") or ""),
        "evidence": evidence,
        "decision": decision,
        "review_summary": summary,
        "why": summary.get("why", ""),
        "save_as": summary.get("save_as", ""),
        "reuse": summary.get("reuse", ""),
        "risk": summary.get("risk", ""),
        "source_task": str(item.get("source_task") or ""),
        "final_preview": str(item.get("final_preview") or ""),
        "session_id": str(item.get("session_id") or ""),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "approved_at": item.get("approved_at"),
        "discarded_at": item.get("discarded_at"),
        "path": artifact_path,
        "artifact": artifact,
        "stats": {},
        "validation": {},
    }


def _skill_review_payload(record: Any) -> dict[str, Any]:
    meta = record.metadata if isinstance(getattr(record, "metadata", None), dict) else {}
    review = meta.get("review") if isinstance(meta.get("review"), dict) else {}
    provenance = meta.get("provenance") if isinstance(meta.get("provenance"), dict) else {}
    memory_decision = meta.get("memory_decision") if isinstance(meta.get("memory_decision"), dict) else {}
    validation = meta.get("validation") if isinstance(meta.get("validation"), dict) else {}
    status = _skill_review_status(str(getattr(record, "status", "") or meta.get("status") or ""), review)
    reason = (
        str(review.get("reason") or "").strip()
        or str(provenance.get("reason") or "").strip()
        or str(memory_decision.get("reason") or "").strip()
    )
    evidence = _review_string_list(review.get("evidence"), limit=6)
    if not evidence and validation.get("issues"):
        evidence = [f"validation: {issue}" for issue in _review_string_list(validation.get("issues"), limit=6)]
    if not evidence and provenance.get("history_tail"):
        evidence = _review_string_list(provenance.get("history_tail"), limit=6)
    stats = meta.get("stats") if isinstance(meta.get("stats"), dict) else {}
    summary = _skill_review_summary(record, meta=meta, review=review, provenance=provenance, decision=memory_decision, validation=validation, evidence=evidence)
    return {
        "id": f"skill:{record.name}",
        "raw_id": record.name,
        "kind": "skill",
        "status": status,
        "target": str(getattr(record, "category", "") or meta.get("category") or "general"),
        "title": str(meta.get("title") or record.name),
        "description": str(meta.get("description") or ""),
        "content": str(getattr(record, "body", "") or ""),
        "body": str(getattr(record, "body", "") or ""),
        "reason": reason,
        "evidence": evidence,
        "decision": copy.deepcopy(memory_decision),
        "review_summary": summary,
        "why": summary.get("why", ""),
        "save_as": summary.get("save_as", ""),
        "reuse": summary.get("reuse", ""),
        "risk": summary.get("risk", ""),
        "source_task": str(provenance.get("task") or ""),
        "final_preview": "",
        "session_id": str(provenance.get("session_id") or ""),
        "created_at": str(meta.get("created_at") or review.get("created_at") or ""),
        "updated_at": str(meta.get("updated_at") or ""),
        "approved_at": review.get("approved_at") or meta.get("promoted_at"),
        "discarded_at": review.get("discarded_at"),
        "path": str(getattr(record, "path", "") or ""),
        "stats": copy.deepcopy(stats),
        "validation": copy.deepcopy(validation),
        "skill_status": str(getattr(record, "status", "") or meta.get("status") or ""),
        "category": str(getattr(record, "category", "") or meta.get("category") or "general"),
        "tags": _review_string_list(meta.get("tags"), limit=12),
        "tools": _review_string_list(meta.get("tools"), limit=12),
    }


def _review_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    status_rank = {"pending": 0, "approved": 1, "discarded": 2}.get(str(item.get("status") or ""), 3)
    timestamp = str(item.get("updated_at") or item.get("created_at") or "")
    return (status_rank, _reverse_timestamp(timestamp), str(item.get("id") or ""))


def _reverse_timestamp(value: str) -> str:
    # ISO timestamps sort lexically ascending; invert printable codepoints for descending order.
    return "".join(chr(255 - min(255, ord(ch))) for ch in value)


def _safe_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _trace_sort_key(node: dict[str, Any]) -> tuple[str, int, str]:
    timestamp = str(node.get("timestamp") or "")
    sequence = _safe_int(node.get("sequence"))
    return (timestamp, sequence, str(node.get("id") or ""))


def _skill_review_status(status: str, review: dict[str, Any]) -> str:
    review_status = _review_status(str(review.get("status") or ""))
    normalized = status.strip().lower()
    if normalized == DRAFT_STATUS:
        return "pending"
    if normalized == ARCHIVED_STATUS:
        return "discarded"
    if normalized == ACTIVE_STATUS or normalized == STALE_STATUS:
        return "approved"
    if review_status in {"pending", "approved", "discarded"}:
        return review_status
    return "pending"


def _review_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"pending", "approved", "discarded"}:
        return normalized
    if normalized in {"active", "stale"}:
        return "approved"
    if normalized in {"archive", "archived", "rejected"}:
        return "discarded"
    return "pending"


def _memory_target_label(target: str) -> str:
    labels = {
        "fact": "Project fact",
        "user_profile": "User preference",
        "sop": "Operating note",
    }
    return labels.get(target, "Memory")


def _review_string_list(value: Any, *, limit: int) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        values = [value]
    result: list[str] = []
    for item in values:
        text = str(item).strip()
        if not text:
            continue
        result.append(text[:240])
        if len(result) >= limit:
            break
    return result


def _review_id_parts(value: str) -> tuple[str, str]:
    raw = value.strip()
    if not raw:
        return "", ""
    if ":" in raw:
        kind, item_id = raw.split(":", 1)
        kind = kind.strip().lower()
        item_id = item_id.strip()
        if kind in {"memory", "skill"}:
            return kind, item_id
        return "", item_id
    if raw.startswith("mem-"):
        return "memory", raw
    return "skill", raw


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _normalize_desktop_gateway_platform(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "qq": "qq",
        "qq_personal": "qq_personal",
        "personal_qq": "qq_personal",
        "onebot": "qq_personal",
        "napcat": "qq_personal",
        "wechat": "wechat",
        "wechat_personal": "wechat",
        "wx": "wechat",
        "weixin": "wechat",
        "feishu": "feishu",
        "lark": "feishu",
        "fs": "feishu",
    }
    return aliases.get(normalized, "")


def _attachment_kind(path: Path) -> str:
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


def _count_tool_turn_cards(history: list[dict[str, Any]]) -> int:
    count = 0
    for message in history:
        if str(message.get("role") or "").lower() != "assistant":
            continue
        blocks = message.get("blocks")
        if not isinstance(blocks, list):
            continue
        if any(
            isinstance(block, dict) and str(block.get("type") or "").lower() == "tool_use"
            for block in blocks
        ):
            count += 1
    return count


def _count_conversation_turns(history: list[dict[str, Any]]) -> int:
    turns = 0
    for message in history:
        if str(message.get("role") or "").lower() != "user":
            continue
        blocks = message.get("blocks")
        if isinstance(blocks, list) and any(
            isinstance(block, dict) and str(block.get("type") or "").lower() == "tool_result"
            for block in blocks
        ):
            continue
        if _message_visible_text(message):
            turns += 1
    return turns


def _message_visible_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        text = content.strip()
        return "" if is_orphaned_tool_result_text(text) else text
    blocks = message.get("blocks")
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("text"), str):
            parts.append(str(block.get("text") or ""))
        elif isinstance(block.get("content"), str):
            parts.append(str(block.get("content") or ""))
    text = "\n".join(part for part in parts if part).strip()
    return "" if is_orphaned_tool_result_text(text) else text


def _path_key(path: str | Path) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(Path(path).expanduser())


def _workspace_kind(path: Path) -> str:
    if path.is_dir():
        return "folder"
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


def _workspace_summary(path: Path, kind: str) -> str:
    if path.is_dir():
        try:
            count = sum(1 for _ in path.iterdir())
        except OSError:
            count = 0
        return f"{count} items"
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if kind == "text":
        return f"text, {_fmt_bytes(size)}"
    if kind == "image":
        return f"image, {_fmt_bytes(size)}"
    return f"file, {_fmt_bytes(size)}"


def _workspace_preview_text(path: Path) -> str:
    if not path.exists() or path.is_dir():
        return ""
    if _workspace_kind(path) == "image":
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) <= _WORKSPACE_PREVIEW_CHARS:
        return text.strip()
    return text[:_WORKSPACE_PREVIEW_CHARS].rstrip() + "\n...[preview truncated]"


def _fmt_bytes(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _normalize_permission_level(value: Any, fallback: str = "balanced") -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "strict": "locked",
        "safe": "locked",
        "ask": "locked",
        "normal": "balanced",
        "default": "balanced",
        "trusted": "full",
        "off": "full",
        "none": "full",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in {"locked", "balanced", "full"}:
        return normalized
    return fallback


def _session_title(history: list[dict[str, Any]]) -> str:
    for msg in history:
        if msg.get("role") != "user":
            continue
        for block in msg.get("blocks", []):
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text", "")).strip().replace("\n", " ")
                if text:
                    return text[:40] if len(text) > 40 else text
    return "Untitled session"


def _elapsed_ms(started: float) -> int:
    return int((datetime.now().timestamp() - started) * 1000)


# __all__：导出本模块所有公开的模块级名字（含 imports），
# 使各 mixin 的 `from ._common import *` 完整复刻原 electron_runtime 命名空间。
__all__ = [name for name in dir() if not name.startswith('__')]
