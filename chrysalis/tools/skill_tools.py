"""Skill library tools."""

from __future__ import annotations

from pathlib import Path

from chrysalis.skills import SkillStore
from chrysalis.tools.registry import tool

_STORE = SkillStore()


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _status_arg(args: dict, default: str = "active") -> str | None:
    status = str(args.get("status") or default).strip().lower()
    if status in {"", "all", "*", "any", "none"}:
        return None
    return status


def _resolve_source_path(source: str, workspace: Path | None) -> Path:
    path = Path(source).expanduser()
    if path.is_absolute():
        return path
    candidates = []
    if workspace is not None:
        candidates.append(workspace / path)
    candidates.append(_STORE.project_root / path)
    candidates.append(Path.cwd() / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


@tool(
    "skill_discover",
    "Find installed Chrysalis skills that may help with the current task.",
    params={
        "query": "Task or capability to search for",
        "top_k": "Maximum matches (default 5)",
        "include_drafts": "Include draft skills",
    },
)
def skill_discover(args: dict, workspace: Path | None = None) -> dict:
    query = str(args.get("query") or "").strip()
    top_k = int(args.get("top_k") or 5)
    include_drafts = _as_bool(args.get("include_drafts"), False)
    matches = [
        record.summary()
        for record in _STORE.search(
            query,
            top_k=top_k,
            status="active",
            include_drafts=include_drafts,
            include_archived=False,
        )
    ]
    next_steps = [
        "Use skill_view(name) before applying a matching skill.",
        "If no installed skill fits and the task will recur, create one with skill_create or install a local skill package with skill_install.",
    ]
    return {"ok": True, "query": query, "matches": matches, "count": len(matches), "next_steps": next_steps}


@tool(
    "skill_install",
    "Install a Chrysalis skill from a local directory containing SKILL.md.",
    params={
        "source": "Local directory or SKILL.md path",
        "category": "Install category (default external)",
        "overwrite": "Overwrite an existing skill with the same name",
    },
)
def skill_install(args: dict, workspace: Path | None = None) -> dict:
    source = str(args.get("source") or "").strip()
    if not source:
        return {"ok": False, "error": "source is required"}
    source_path = _resolve_source_path(source, workspace)
    return _STORE.install_from_path(
        source_path,
        category=str(args.get("category") or "external"),
        overwrite=_as_bool(args.get("overwrite"), False),
    )


@tool(
    "skill_list",
    "List skills with metadata.",
    params={
        "category": "Optional category filter",
        "status": "Filter by status: active, stale, draft, archived, or all",
    },
)
def skill_list(args: dict, workspace: Path | None = None) -> dict:
    status = _status_arg(args)
    category = str(args.get("category") or "").strip() or None
    skills = [
        record.summary()
        for record in _STORE.list_skills(
            status=status,
            category=category,
            include_drafts=True,
            include_archived=True,
        )
    ]
    return {"ok": True, "skills": skills, "count": len(skills)}


@tool(
    "skill_search",
    "Search related skills.",
    params={
        "query": "Search text",
        "top_k": "Maximum results (default 5)",
        "status": "Optional status filter",
        "include_drafts": "Include draft skills",
        "include_archived": "Include archived skills",
    },
)
def skill_search(args: dict, workspace: Path | None = None) -> dict:
    query = str(args.get("query") or "").strip()
    top_k = int(args.get("top_k") or 5)
    status = _status_arg(args)
    include_drafts = _as_bool(args.get("include_drafts"), False)
    include_archived = _as_bool(args.get("include_archived"), False)
    skills = [
        record.summary()
        for record in _STORE.search(
            query,
            top_k=top_k,
            status=status,
            include_drafts=include_drafts,
            include_archived=include_archived,
        )
    ]
    return {"ok": True, "query": query, "skills": skills, "count": len(skills)}


@tool(
    "skill_view",
    "View a skill file or linked asset.",
    params={
        "name": "Skill name or id",
        "file_path": "Optional path inside the skill directory",
    },
)
def skill_view(args: dict, workspace: Path | None = None) -> dict:
    return _STORE.view(str(args.get("name") or ""), str(args.get("file_path") or ""))


@tool(
    "skill_create",
    "Create a draft or active skill.",
    params={
        "name": "Skill name",
        "description": "Short description",
        "body": "Skill body",
        "category": "Skill category",
        "status": "draft or active",
    },
)
def skill_create(args: dict, workspace: Path | None = None) -> dict:
    tags = args.get("tags") or []
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",") if part.strip()]
    metadata = args.get("metadata") if isinstance(args.get("metadata"), dict) else None
    record = _STORE.create(
        name=str(args.get("name") or ""),
        description=str(args.get("description") or ""),
        body=str(args.get("body") or ""),
        category=str(args.get("category") or "general"),
        tags=[str(tag) for tag in tags],
        status=str(args.get("status") or "draft").strip().lower() or "draft",
        metadata=metadata,
    )
    return {"ok": True, "skill": record.summary()}


@tool(
    "skill_promote",
    "Promote a draft skill to active.",
    params={"name": "Skill name or id"},
)
def skill_promote(args: dict, workspace: Path | None = None) -> dict:
    return _STORE.promote(str(args.get("name") or ""))


@tool(
    "skill_archive",
    "Archive a skill.",
    params={"name": "Skill name or id"},
)
def skill_archive(args: dict, workspace: Path | None = None) -> dict:
    return _STORE.archive(str(args.get("name") or ""))


@tool(
    "skill_restore",
    "Restore an archived skill.",
    params={"name": "Skill name or id"},
)
def skill_restore(args: dict, workspace: Path | None = None) -> dict:
    return _STORE.restore(str(args.get("name") or ""))


@tool(
    "skill_pin",
    "Pin or unpin a skill.",
    params={
        "name": "Skill name or id",
        "pinned": "true or false",
    },
)
def skill_pin(args: dict, workspace: Path | None = None) -> dict:
    return _STORE.set_pinned(str(args.get("name") or ""), _as_bool(args.get("pinned"), True))


@tool(
    "skill_status",
    "Set a skill lifecycle state.",
    params={
        "name": "Skill name or id",
        "status": "active, stale, draft, or archived",
    },
)
def skill_status(args: dict, workspace: Path | None = None) -> dict:
    return _STORE.set_status(str(args.get("name") or ""), str(args.get("status") or ""))


@tool(
    "skill_curate",
    "Run skill lifecycle maintenance.",
    params={
        "stale_after_days": "Days until a skill becomes stale",
        "archive_after_days": "Days until a skill is archived",
        "dry_run": "Preview without writing changes",
    },
)
def skill_curate(args: dict, workspace: Path | None = None) -> dict:
    stale_after_days = int(args.get("stale_after_days") or 30)
    archive_after_days = int(args.get("archive_after_days") or 90)
    dry_run = _as_bool(args.get("dry_run"), False)
    return _STORE.curate_lifecycle(
        stale_after_days=stale_after_days,
        archive_after_days=archive_after_days,
        dry_run=dry_run,
    )
