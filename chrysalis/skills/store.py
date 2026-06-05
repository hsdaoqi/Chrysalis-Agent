"""Local skill-note library.

Skills are intentionally lightweight, GA-style SOP directories:

skills/<name>/
  skill.json   machine-readable metadata
  SKILL.md     the short human/model-readable SOP entry point
  *.py         optional helper scripts used by the SOP

Older category subdirectories are still readable for compatibility, but new
active skills are written to the flat ``skills/<name>`` layout.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from configs.config import PROJECT_ROOT

SKILL_JSON = "skill.json"
SKILL_MD = "SKILL.md"
ACTIVE_STATUS = "active"
DRAFT_STATUS = "draft"
ARCHIVED_STATUS = "archived"
STALE_STATUS = "stale"
HIDDEN_DIRS = {".archive", ".drafts", "__pycache__"}
DEFAULT_STATS = {
    "uses": 0,
    "successes": 0,
    "failures": 0,
    "matches": 0,
    "views": 0,
    "last_used_at": None,
    "last_matched_at": None,
    "last_viewed_at": None,
}


@dataclass
class SkillRecord:
    path: Path
    metadata: dict[str, Any]
    body: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return str(self.metadata.get("id") or self.metadata.get("name") or self.path.name)

    @property
    def name(self) -> str:
        return str(self.metadata.get("name") or self.path.name)

    @property
    def status(self) -> str:
        return str(self.metadata.get("status") or ACTIVE_STATUS)

    @property
    def category(self) -> str:
        return str(self.metadata.get("category") or "")

    def summary(self) -> dict[str, Any]:
        stats = _normalized_stats(self.metadata.get("stats"))
        return {
            "id": self.id,
            "name": self.name,
            "title": self.metadata.get("title") or self.name,
            "description": self.metadata.get("description", ""),
            "category": self.category,
            "tags": self.metadata.get("tags", []),
            "status": self.status,
            "pinned": bool(self.metadata.get("pinned", False)),
            "version": self.metadata.get("version", "1.0.0"),
            "stats": stats,
            "validation": self.metadata.get("validation", {}),
            "path": str(self.path),
            "score": round(self.score, 3) if self.score else 0,
            "reasons": self.reasons,
        }


class SkillStore:
    """Filesystem-backed skill store with zero external dependencies."""

    def __init__(self, skills_dir: Path | None = None, project_root: Path | None = None) -> None:
        self.project_root = project_root or PROJECT_ROOT
        self.skills_dir = skills_dir or self.project_root / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def list_skills(
        self,
        *,
        status: str | None = ACTIVE_STATUS,
        category: str | None = None,
        include_drafts: bool = False,
        include_archived: bool = False,
    ) -> list[SkillRecord]:
        records: list[SkillRecord] = []
        for meta_path in self.skills_dir.rglob(SKILL_JSON):
            if self._skip_path(meta_path, include_drafts=include_drafts, include_archived=include_archived):
                continue
            record = self._load_record(meta_path.parent)
            if record is None:
                continue
            if status and record.status != status:
                continue
            if category and record.category != category:
                continue
            records.append(record)
        return sorted(
            records,
            key=lambda rec: (
                0 if rec.metadata.get("pinned") else 1,
                _status_rank(rec.status),
                rec.category,
                rec.name,
            ),
        )

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        status: str | None = ACTIVE_STATUS,
        include_drafts: bool = False,
        include_archived: bool = False,
    ) -> list[SkillRecord]:
        query = query.strip()
        if not query:
            return []
        query_tokens = _tokens(query)
        if not query_tokens:
            return []

        scored: list[SkillRecord] = []
        for record in self.list_skills(
            status=status,
            include_drafts=include_drafts,
            include_archived=include_archived,
        ):
            haystack = _skill_search_text(record)
            hay_tokens = set(_tokens(haystack))
            score = 0.0
            reasons: list[str] = []

            lowered_query = query.lower()
            lowered_haystack = haystack.lower()
            if lowered_query and lowered_query in lowered_haystack:
                score += 1.5
                reasons.append("phrase")

            overlap = [token for token in query_tokens if token in hay_tokens or token in lowered_haystack]
            if overlap:
                score += len(overlap) / max(1, len(query_tokens))
                reasons.append("tokens:" + ",".join(overlap[:5]))

            tags = {str(tag).lower() for tag in record.metadata.get("tags", [])}
            tag_hits = [token for token in query_tokens if token in tags]
            if tag_hits:
                score += 0.7 + 0.15 * len(tag_hits)
                reasons.append("tags:" + ",".join(tag_hits[:5]))

            tools = {str(tool).lower() for tool in record.metadata.get("tools", [])}
            tool_hits = [token for token in query_tokens if token in tools]
            if tool_hits:
                score += 0.4
                reasons.append("tools:" + ",".join(tool_hits[:3]))

            field_score, field_reasons = _weighted_field_score(record, query_tokens, lowered_query)
            if field_score:
                score += field_score
                reasons.extend(field_reasons)

            quality_score, quality_reasons = _quality_score(record)
            if quality_score:
                score += quality_score
                reasons.extend(quality_reasons)

            if score <= 0:
                continue
            record.score = score
            record.reasons = reasons
            scored.append(record)

        scored.sort(key=lambda rec: rec.score, reverse=True)
        return scored[: max(1, top_k)]

    def view(self, name: str, file_path: str = "") -> dict[str, Any]:
        record = self.find(name, include_drafts=True, include_archived=True)
        if record is None:
            return {"ok": False, "error": f"skill not found: {name}"}

        target = record.path / SKILL_MD
        if file_path:
            if _has_traversal(file_path):
                return {"ok": False, "error": "path traversal is not allowed"}
            target = (record.path / file_path).resolve()
            try:
                target.relative_to(record.path.resolve())
            except ValueError:
                return {"ok": False, "error": "file_path must stay inside the skill directory"}
        if not target.exists() or not target.is_file():
            return {
                "ok": False,
                "error": f"file not found: {file_path or SKILL_MD}",
                "available_files": self._available_files(record.path),
            }
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            self.record_view(record.name)
            return {
                "ok": True,
                "name": record.name,
                "file": str(target.relative_to(record.path)),
                "content": f"[Binary file: {target.name}, size={target.stat().st_size} bytes]",
                "is_binary": True,
            }
        self.record_view(record.name)
        return {
            "ok": True,
            "metadata": record.metadata,
            "name": record.name,
            "file": str(target.relative_to(record.path)),
            "content": content,
            "linked_files": self._available_files(record.path),
        }

    def find(
        self,
        name: str,
        *,
        include_drafts: bool = False,
        include_archived: bool = False,
    ) -> SkillRecord | None:
        needle = name.strip().lower()
        if not needle:
            return None
        for record in self.list_skills(
            status=None,
            include_drafts=include_drafts,
            include_archived=include_archived,
        ):
            aliases = {
                record.id.lower(),
                record.name.lower(),
                str(record.metadata.get("title", "")).lower(),
                str(record.path.name).lower(),
            }
            if needle in aliases:
                return record
        return None

    def create(
        self,
        *,
        name: str,
        description: str,
        body: str,
        category: str = "general",
        tags: list[str] | None = None,
        status: str = DRAFT_STATUS,
        metadata: dict[str, Any] | None = None,
    ) -> SkillRecord:
        name_slug = _slug(name)
        category_slug = _slug(category or "general")
        if status == DRAFT_STATUS:
            target = self.skills_dir / ".drafts" / name_slug
        else:
            target = self.skills_dir / name_slug
        target.mkdir(parents=True, exist_ok=True)

        now = _now()
        meta = {
            "id": name_slug,
            "name": name_slug,
            "title": name.strip() or name_slug,
            "description": description.strip(),
            "category": category_slug,
            "tags": tags or [],
            "status": status,
            "version": "1.0.0",
            "created_at": now,
            "updated_at": now,
            "stats": dict(DEFAULT_STATS),
            "pinned": False,
        }
        if metadata:
            meta.update(metadata)
            meta["id"] = str(meta.get("id") or name_slug)
            meta.setdefault("name", name_slug)
            meta.setdefault("status", status)
        meta["stats"] = _normalized_stats(meta.get("stats"))
        meta["pinned"] = bool(meta.get("pinned", False))

        _write_json_atomic(target / SKILL_JSON, meta)
        (target / SKILL_MD).write_text(_normalize_skill_body(meta, body), encoding="utf-8")
        record = self._load_record(target) or SkillRecord(target, meta, body)
        if record.status == ACTIVE_STATUS:
            self._record_l1_pointer(record)
        return record

    def install_from_path(
        self,
        source: str | Path,
        *,
        category: str = "external",
        status: str = ACTIVE_STATUS,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        source_path = Path(source).expanduser()
        if source_path.is_file():
            source_path = source_path.parent
        try:
            source_path = source_path.resolve()
        except OSError:
            return {"ok": False, "error": f"source path not found: {source}"}
        skill_md = source_path / SKILL_MD
        if not skill_md.exists():
            return {"ok": False, "error": f"SKILL.md not found under: {source_path}"}

        body = skill_md.read_text(encoding="utf-8", errors="replace")
        source_meta = _read_json(source_path / SKILL_JSON) if (source_path / SKILL_JSON).exists() else {}
        frontmatter, _ = _parse_skill_frontmatter(body)
        name = str(source_meta.get("name") or frontmatter.get("name") or source_path.name)
        name_slug = _slug(name)
        category_slug = _slug(str(source_meta.get("category") or category or "external"))
        target = (self.skills_dir / name_slug).resolve()
        if not _is_relative_to(target, self.skills_dir.resolve()):
            return {"ok": False, "error": "resolved target escaped skills directory"}
        if source_path == target:
            record = self._load_record(target)
            if record and record.status == ACTIVE_STATUS:
                self._record_l1_pointer(record)
            return {"ok": True, "installed": False, "message": "skill already installed", "skill": record.summary() if record else None}
        if target.exists():
            if not overwrite:
                return {"ok": False, "error": f"skill already exists: {name_slug}", "target": str(target)}
            shutil.rmtree(target)

        ignore = shutil.ignore_patterns(".git", "__pycache__", "*.pyc")
        shutil.copytree(source_path, target, ignore=ignore)

        now = _now()
        meta = dict(source_meta)
        meta.update({key: value for key, value in frontmatter.items() if key not in meta})
        meta.setdefault("id", name_slug)
        meta["name"] = name_slug
        meta.setdefault("title", str(frontmatter.get("title") or name).strip() or name_slug)
        meta.setdefault("description", str(frontmatter.get("description") or "").strip())
        meta["category"] = category_slug
        meta["status"] = status or ACTIVE_STATUS
        meta.setdefault("version", "1.0.0")
        meta.setdefault("created_at", now)
        meta["updated_at"] = now
        meta["stats"] = _normalized_stats(meta.get("stats"))
        meta["pinned"] = bool(meta.get("pinned", False))
        meta.setdefault("source", {})
        if isinstance(meta["source"], dict):
            meta["source"].setdefault("type", "local_path")
            meta["source"].setdefault("path", str(source_path))

        _write_json_atomic(target / SKILL_JSON, meta)
        record = self._load_record(target)
        if record and record.status == ACTIVE_STATUS:
            self._record_l1_pointer(record)
        return {"ok": True, "installed": True, "skill": record.summary() if record else meta}

    def update(
        self,
        name: str,
        *,
        metadata: dict[str, Any] | None = None,
        body: str | None = None,
        include_drafts: bool = True,
        include_archived: bool = True,
    ) -> SkillRecord | None:
        record = self.find(name, include_drafts=include_drafts, include_archived=include_archived)
        if record is None:
            return None
        next_meta = dict(record.metadata)
        if metadata:
            next_meta = _merge_metadata(next_meta, metadata)
        next_meta["updated_at"] = _now()
        if body is not None:
            (record.path / SKILL_MD).write_text(_normalize_skill_body(next_meta, body), encoding="utf-8")
        _write_json_atomic(record.path / SKILL_JSON, next_meta)
        return self._load_record(record.path)

    def promote(self, name: str) -> dict[str, Any]:
        record = self.find(name, include_drafts=True)
        if record is None:
            return {"ok": False, "error": f"skill not found: {name}"}
        if record.status == ACTIVE_STATUS:
            return {"ok": True, "message": "skill already active", "skill": record.summary()}

        meta = dict(record.metadata)
        meta["status"] = ACTIVE_STATUS
        meta["updated_at"] = _now()
        name_slug = _slug(str(meta.get("name") or record.path.name))
        target = self.skills_dir / name_slug
        if target.exists():
            return {"ok": False, "error": f"target skill already exists: {target}"}
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(record.path), str(target))
        _write_json_atomic(target / SKILL_JSON, meta)
        promoted = self._load_record(target)
        if promoted:
            self._record_l1_pointer(promoted)
        return {"ok": True, "message": "skill promoted", "skill": promoted.summary() if promoted else meta}

    def archive(self, name: str) -> dict[str, Any]:
        record = self.find(name, include_drafts=True, include_archived=True)
        if record is None:
            return {"ok": False, "error": f"skill not found: {name}"}
        if ".archive" in record.path.parts:
            return {"ok": True, "message": "skill already archived", "skill": record.summary()}

        meta = dict(record.metadata)
        meta["status"] = ARCHIVED_STATUS
        meta["updated_at"] = _now()
        target = self.skills_dir / ".archive" / record.path.name
        if target.exists():
            target = self.skills_dir / ".archive" / f"{record.path.name}-{datetime.now():%Y%m%d%H%M%S}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(record.path), str(target))
        _write_json_atomic(target / SKILL_JSON, meta)
        return {"ok": True, "message": "skill archived", "skill": self._load_record(target).summary()}

    def delete(self, name: str) -> dict[str, Any]:
        record = self.find(name, include_drafts=True, include_archived=True)
        if record is None:
            return {"ok": False, "error": f"skill not found: {name}"}
        skills_root = self.skills_dir.resolve()
        target = record.path.resolve()
        if target == skills_root or not _is_relative_to(target, skills_root):
            return {"ok": False, "error": f"refusing to delete outside skills directory: {target}"}
        summary = record.summary()
        shutil.rmtree(target)
        return {"ok": True, "message": "skill deleted", "path": str(target), "skill": summary}

    def restore(self, name: str) -> dict[str, Any]:
        record = self.find(name, include_drafts=True, include_archived=True)
        if record is None:
            return {"ok": False, "error": f"skill not found: {name}"}
        if ".archive" not in record.path.parts and record.status != ARCHIVED_STATUS:
            return {"ok": True, "message": "skill is not archived", "skill": record.summary()}

        meta = dict(record.metadata)
        meta["status"] = ACTIVE_STATUS
        meta["updated_at"] = _now()
        name_slug = _slug(str(meta.get("name") or record.path.name))
        target = self.skills_dir / name_slug
        if target.exists():
            return {"ok": False, "error": f"target skill already exists: {target}"}
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(record.path), str(target))
        _write_json_atomic(target / SKILL_JSON, meta)
        restored = self._load_record(target)
        if restored:
            self._record_l1_pointer(restored)
        return {"ok": True, "message": "skill restored", "skill": restored.summary() if restored else meta}

    def set_pinned(self, name: str, pinned: bool) -> dict[str, Any]:
        record = self.update(
            name,
            metadata={"pinned": bool(pinned)},
            include_drafts=True,
            include_archived=True,
        )
        if record is None:
            return {"ok": False, "error": f"skill not found: {name}"}
        return {"ok": True, "message": "skill pin updated", "skill": record.summary()}

    def set_status(self, name: str, status: str) -> dict[str, Any]:
        status = status.strip().lower()
        if status not in {ACTIVE_STATUS, DRAFT_STATUS, ARCHIVED_STATUS, STALE_STATUS}:
            return {"ok": False, "error": f"invalid status: {status}"}
        if status == ARCHIVED_STATUS:
            return self.archive(name)
        if status == ACTIVE_STATUS:
            record = self.find(name, include_drafts=True, include_archived=True)
            if record and (".archive" in record.path.parts or record.status == ARCHIVED_STATUS):
                return self.restore(name)
            if record and (".drafts" in record.path.parts or record.status == DRAFT_STATUS):
                return self.promote(name)
        record = self.update(name, metadata={"status": status}, include_drafts=True, include_archived=True)
        if record is None:
            return {"ok": False, "error": f"skill not found: {name}"}
        if record.status == ACTIVE_STATUS:
            self._record_l1_pointer(record)
        return {"ok": True, "message": "skill status updated", "skill": record.summary()}

    def record_match(self, name: str) -> None:
        record = self.find(name)
        if record is None:
            return
        meta = dict(record.metadata)
        stats = _normalized_stats(meta.get("stats"))
        stats["matches"] = int(stats.get("matches") or 0) + 1
        stats["last_matched_at"] = _now()
        meta["stats"] = stats
        meta["updated_at"] = _now()
        _write_json_atomic(record.path / SKILL_JSON, meta)

    def record_view(self, name: str) -> None:
        record = self.find(name, include_drafts=True, include_archived=True)
        if record is None:
            return
        meta = dict(record.metadata)
        stats = _normalized_stats(meta.get("stats"))
        stats["views"] = int(stats.get("views") or 0) + 1
        stats["last_viewed_at"] = _now()
        meta["stats"] = stats
        meta["updated_at"] = _now()
        _write_json_atomic(record.path / SKILL_JSON, meta)

    def record_use(self, name: str, success: bool | None = None) -> None:
        record = self.find(name, include_drafts=True)
        if record is None:
            return
        meta = dict(record.metadata)
        stats = _normalized_stats(meta.get("stats"))
        stats["uses"] = int(stats.get("uses") or 0) + 1
        if success is True:
            stats["successes"] = int(stats.get("successes") or 0) + 1
        elif success is False:
            stats["failures"] = int(stats.get("failures") or 0) + 1
        stats["last_used_at"] = _now()
        meta["stats"] = stats
        became_active = False
        if success is True and meta.get("status") == STALE_STATUS:
            meta["status"] = ACTIVE_STATUS
            became_active = True
        meta["updated_at"] = _now()
        _write_json_atomic(record.path / SKILL_JSON, meta)
        if became_active:
            refreshed = self._load_record(record.path)
            if refreshed:
                self._record_l1_pointer(refreshed)

    def curate_lifecycle(
        self,
        *,
        stale_after_days: int = 30,
        archive_after_days: int = 90,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        now = datetime.now()
        changes: list[dict[str, Any]] = []
        stale_after = timedelta(days=max(1, stale_after_days))
        archive_after = timedelta(days=max(stale_after_days + 1, archive_after_days))

        for record in self.list_skills(status=None, include_drafts=True, include_archived=True):
            if record.status in {DRAFT_STATUS, ARCHIVED_STATUS}:
                continue
            if record.metadata.get("pinned"):
                continue

            last_activity = _latest_activity(record)
            if last_activity is None:
                last_activity = _parse_time(record.metadata.get("created_at")) or now
            age = now - last_activity
            next_status = record.status
            action = ""
            if age >= archive_after:
                next_status = ARCHIVED_STATUS
                action = "archive"
            elif age >= stale_after and record.status == ACTIVE_STATUS:
                next_status = STALE_STATUS
                action = "mark_stale"

            if not action:
                continue
            change = {
                "name": record.name,
                "from": record.status,
                "to": next_status,
                "action": action,
                "last_activity_at": last_activity.isoformat(timespec="seconds"),
                "age_days": age.days,
                "dry_run": dry_run,
            }
            changes.append(change)
            if dry_run:
                continue
            if action == "archive":
                archived = self.archive(record.name)
                change["ok"] = bool(archived.get("ok"))
                if archived.get("error"):
                    change["error"] = archived["error"]
            else:
                updated = self.update(record.name, metadata={"status": STALE_STATUS}, include_drafts=True)
                change["ok"] = updated is not None

        return {"ok": True, "changes": changes, "count": len(changes), "dry_run": dry_run}

    def _load_record(self, path: Path) -> SkillRecord | None:
        meta_path = path / SKILL_JSON
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        body = ""
        try:
            body = (path / SKILL_MD).read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        return SkillRecord(path=path, metadata=metadata, body=body)

    def _skip_path(self, meta_path: Path, *, include_drafts: bool, include_archived: bool) -> bool:
        parts = set(meta_path.relative_to(self.skills_dir).parts)
        if "__pycache__" in parts:
            return True
        if ".drafts" in parts and not include_drafts:
            return True
        if ".archive" in parts and not include_archived:
            return True
        return bool(parts & (HIDDEN_DIRS - {".drafts", ".archive"}))

    def _available_files(self, skill_dir: Path) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {
            "references": [],
            "templates": [],
            "scripts": [],
            "assets": [],
            "other": [],
        }
        for path in skill_dir.rglob("*"):
            if not path.is_file() or path.name in {SKILL_JSON, SKILL_MD}:
                continue
            rel = path.relative_to(skill_dir).as_posix()
            top = rel.split("/", 1)[0]
            if top in groups:
                groups[top].append(rel)
            elif path.suffix.lower() == ".py":
                groups["scripts"].append(rel)
            else:
                groups["other"].append(rel)
        return {key: sorted(value) for key, value in groups.items() if value}

    def _record_l1_pointer(self, record: SkillRecord) -> None:
        pointer = _skill_l1_pointer(record.path, self.project_root)
        if not pointer:
            return
        path = self.project_root / "memory" / "global_mem_insight.txt"
        try:
            previous = path.read_text(encoding="utf-8")
        except OSError:
            previous = ""
        existing = {line.strip() for line in previous.splitlines()}
        if pointer in existing:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = previous.rstrip()
        next_text = f"{prefix}\n{pointer}\n" if prefix else f"{pointer}\n"
        path.write_text(next_text, encoding="utf-8")


def _skill_search_text(record: SkillRecord) -> str:
    meta = record.metadata
    parts = [
        record.id,
        record.name,
        str(meta.get("title", "")),
        str(meta.get("description", "")),
        str(meta.get("category", "")),
        " ".join(str(tag) for tag in meta.get("tags", [])),
        " ".join(str(item) for item in meta.get("when_to_use", [])),
        " ".join(str(item) for item in meta.get("tools", [])),
        " ".join(str(item) for item in meta.get("sop_refs", [])),
        record.body[:4_000],
    ]
    return "\n".join(parts)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_skill_frontmatter(body: str) -> tuple[dict[str, Any], str]:
    body = body.lstrip("\ufeff")
    if not body.startswith("---"):
        return {}, body
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", body, flags=re.DOTALL)
    if not match:
        return {}, body
    meta: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            meta[key] = value
    return meta, body[match.end():]


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _tokens(text: str) -> list[str]:
    lowered = text.lower()
    words = re.findall(r"[a-z0-9_./-]+", lowered)
    cjk = re.findall(r"[\u4e00-\u9fff]", lowered)
    grams = ["".join(cjk[i:i + 2]) for i in range(max(0, len(cjk) - 1))]
    return [token for token in words + cjk + grams if len(token) > 1 or "\u4e00" <= token <= "\u9fff"]


def _slug(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    return text or "skill"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, default=str)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _skill_l1_pointer(skill_dir: Path, project_root: Path) -> str:
    try:
        return (skill_dir / SKILL_MD).relative_to(project_root).as_posix()
    except ValueError:
        try:
            return (skill_dir / SKILL_MD).as_posix()
        except OSError:
            return ""


def _normalize_skill_body(meta: dict[str, Any], body: str) -> str:
    body = body.strip()
    if body:
        return _ensure_about_section(meta, body) + "\n"
    title = meta.get("title") or meta.get("name") or "Skill"
    description = meta.get("description", "")
    lines = [
        f"# {title}",
        "",
        "## About",
        description or "Short note describing when this skill note should be used.",
        "",
        "## When To Use",
        "- Add concrete triggers here.",
        "",
        "## Steps",
        "1. Describe the reusable workflow.",
        "",
        "## Failure Modes",
        "- Add known risks and recovery paths.",
        "",
    ]
    return "\n".join(lines)


def _ensure_about_section(meta: dict[str, Any], body: str) -> str:
    body = body.strip()
    if re.search(r"^##\s+About\b", body, flags=re.IGNORECASE | re.MULTILINE):
        return body
    description = str(meta.get("description") or "").strip()
    if not description:
        return body
    lines = body.splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        return "\n".join([
            lines[0].rstrip(),
            "",
            "## About",
            description,
            "",
            *lines[1:],
        ]).strip()
    return "\n".join([
        "## About",
        description,
        "",
        body,
    ]).strip()


def _extract_key_steps(body: str, limit: int = 5) -> list[str]:
    steps: list[str] = []
    in_steps = False
    for raw in body.splitlines():
        line = raw.strip()
        if line.lower().startswith("## steps"):
            in_steps = True
            continue
        if in_steps and line.startswith("## "):
            break
        if not in_steps:
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        if line:
            steps.append(line)
        if len(steps) >= limit:
            break
    return steps


def _has_traversal(path: str) -> bool:
    return any(part == ".." for part in Path(path).parts)


def _merge_metadata(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    list_keys = {"tags", "tools", "sop_refs", "when_to_use", "key_steps", "safety_notes"}
    for key, value in patch.items():
        if value is None:
            continue
        if key in list_keys:
            merged[key] = _merge_unique_lists(merged.get(key), value)
        elif key == "stats" and isinstance(value, dict):
            stats = dict(merged.get("stats") or {})
            for stat_key, stat_value in value.items():
                stats[stat_key] = stat_value
            merged["stats"] = stats
        elif key == "validation" and isinstance(value, dict):
            current = dict(merged.get("validation") or {})
            current.update(value)
            merged["validation"] = current
        elif key == "provenance" and isinstance(value, dict):
            current = dict(merged.get("provenance") or {})
            current.update(value)
            merged["provenance"] = current
        else:
            merged[key] = value
    return merged


def _merge_unique_lists(existing: Any, incoming: Any) -> list[Any]:
    items = []
    for value in _normalize_list(existing) + _normalize_list(incoming):
        if value not in items:
            items.append(value)
    return items


def _normalize_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _normalized_stats(value: Any) -> dict[str, Any]:
    stats = dict(DEFAULT_STATS)
    if isinstance(value, dict):
        stats.update(value)
    return stats


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _latest_activity(record: SkillRecord) -> datetime | None:
    meta = record.metadata
    candidates = []
    stats = _normalized_stats(meta.get("stats"))
    for key in ("last_used_at", "last_matched_at", "last_viewed_at"):
        dt = _parse_time(stats.get(key))
        if dt is not None:
            candidates.append(dt)
    for key in ("updated_at", "created_at"):
        dt = _parse_time(meta.get(key))
        if dt is not None:
            candidates.append(dt)
    return max(candidates) if candidates else None


def _status_rank(status: str) -> int:
    order = {
        ACTIVE_STATUS: 0,
        STALE_STATUS: 1,
        DRAFT_STATUS: 2,
        ARCHIVED_STATUS: 3,
    }
    return order.get(status, 9)


def _weighted_field_score(record: SkillRecord, query_tokens: list[str], lowered_query: str) -> tuple[float, list[str]]:
    meta = record.metadata
    reasons: list[str] = []
    score = 0.0
    field_weights = [
        ("title", str(meta.get("title", "")), 0.7),
        ("description", str(meta.get("description", "")), 0.35),
        ("body", record.body, 0.15),
        ("category", str(meta.get("category", "")), 0.2),
    ]
    for field_name, value, weight in field_weights:
        if not value:
            continue
        lowered = value.lower()
        if lowered_query and lowered_query in lowered:
            score += weight
            reasons.append(f"{field_name}:phrase")
            continue
        tokens = set(_tokens(value))
        hits = [token for token in query_tokens if token in tokens]
        if hits:
            score += weight * min(1.0, len(hits) / max(1, len(query_tokens)))
            reasons.append(f"{field_name}:{','.join(hits[:3])}")
    return score, reasons


def _quality_score(record: SkillRecord) -> tuple[float, list[str]]:
    meta = record.metadata
    stats = _normalized_stats(meta.get("stats"))
    reasons: list[str] = []
    score = 0.0

    if meta.get("pinned"):
        score += 0.6
        reasons.append("pinned")

    success = int(stats.get("successes") or 0)
    uses = int(stats.get("uses") or 0)
    failures = int(stats.get("failures") or 0)
    matches = int(stats.get("matches") or 0)
    views = int(stats.get("views") or 0)
    if uses:
        success_rate = success / max(1, uses)
        score += min(0.7, 0.45 * success_rate)
        reasons.append(f"success_rate:{success_rate:.2f}")
        score += min(0.25, math.log1p(uses) * 0.08)
        reasons.append(f"uses:{uses}")
    if matches:
        score += min(0.2, math.log1p(matches) * 0.05)
        reasons.append(f"matches:{matches}")
    if views:
        score += min(0.1, math.log1p(views) * 0.03)
        reasons.append(f"views:{views}")
    if failures:
        penalty = min(0.5, failures * 0.12)
        score -= penalty
        reasons.append(f"failures:{failures}")

    updated_at = _parse_time(meta.get("updated_at"))
    if updated_at:
        age_days = max(0.0, (datetime.now() - updated_at).total_seconds() / 86400.0)
        freshness = max(0.0, 1.0 - min(1.0, age_days / 180.0))
        if freshness:
            score += min(0.25, freshness * 0.25)
            reasons.append(f"freshness:{freshness:.2f}")

    if record.status == STALE_STATUS:
        score -= 0.35
        reasons.append("stale")
    elif record.status == DRAFT_STATUS:
        score -= 0.25
        reasons.append("draft")

    return score, reasons
