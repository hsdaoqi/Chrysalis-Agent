"""WorkspaceMixin：拆分自 electron_runtime.py（方法体逐字符保留）。"""
from __future__ import annotations

from chrysalis.electron_runtime._common import *  # noqa: F401,F403


class WorkspaceMixin:
    def _workspace_snapshot(self) -> dict[str, Any]:
        return {
            "root": str(self.kernel.config.workspace_dir),
            "entries": self._workspace_entries(),
            "changes": copy.deepcopy(self._workspace_changes),
            "preview": copy.deepcopy(self._workspace_preview),
        }

    def _workspace_entries(self) -> list[dict[str, Any]]:
        root = self.kernel.config.workspace_dir
        if not root.exists():
            return []
        entries: list[dict[str, Any]] = []

        def visit(directory: Path, depth: int) -> None:
            try:
                children = sorted(
                    directory.iterdir(),
                    key=lambda item: (not item.is_dir(), item.name.lower()),
                )
            except OSError:
                children = []
            for child in children:
                if child.is_dir() and child.name in _IGNORED_WORKSPACE_DIRS:
                    continue
                child_depth = max(depth + 1, 0)
                key = _path_key(child)
                is_dir = child.is_dir()
                has_children = is_dir and self._directory_has_children(child)
                entries.append(
                    {
                        "path": str(child),
                        "name": child.name or str(child),
                        "depth": child_depth,
                        "is_dir": is_dir,
                        "has_children": has_children,
                        "expanded": bool(is_dir and key in self._workspace_expanded),
                        "selected": key == self._workspace_selected_key(),
                        "kind": _workspace_kind(child),
                        "summary": _workspace_summary(child, _workspace_kind(child)),
                    }
                )
                if is_dir and key in self._workspace_expanded:
                    visit(child, child_depth)

        visit(root, -1)
        return entries

    def _workspace_selected_key(self) -> str:
        return _path_key(self._workspace_selected_path) if self._workspace_selected_path else ""

    def _directory_has_children(self, path: Path) -> bool:
        try:
            for child in path.iterdir():
                if child.is_dir() and child.name in _IGNORED_WORKSPACE_DIRS:
                    continue
                return True
        except OSError:
            return False
        return False

    def _expand_ancestors(self, target: Path) -> None:
        root = self.kernel.config.workspace_dir.resolve()
        for parent in target.parents:
            try:
                parent.relative_to(root)
            except ValueError:
                break
            self._workspace_expanded.add(_path_key(parent))
            if _path_key(parent) == _path_key(root):
                break

    def _refresh_workspace_state(self, emit: bool = True) -> None:
        root = self.kernel.config.workspace_dir
        root.mkdir(parents=True, exist_ok=True)
        selected = self._workspace_selected_path
        if selected:
            target = self._workspace_path(selected)
            if target is None:
                selected = ""
                self._workspace_selected_path = ""
            else:
                self._workspace_selected_path = str(target)
                selected = self._workspace_selected_path
        if selected:
            self._workspace_preview = self._build_workspace_preview(Path(selected))
        else:
            self._workspace_preview = self._default_workspace_preview()
        if emit:
            self._emit_event("workspace_changed", snapshot=self._snapshot())

    def _build_workspace_preview(self, target: Path) -> dict[str, Any]:
        kind = _workspace_kind(target)
        summary = _workspace_summary(target, kind) if target.exists() else "missing"
        image_url = ""
        content = ""
        can_preview = False
        if target.is_dir():
            try:
                entries = sorted(item.name for item in target.iterdir())[:80]
            except OSError:
                entries = []
            content = "\n".join(entries) if entries else "(empty folder)"
            can_preview = True
        elif kind == "text":
            content = _workspace_preview_text(target)
            can_preview = bool(content)
        elif kind == "image":
            image_url = target.resolve().as_uri()
            content = "Image preview"
            can_preview = True
        else:
            content = "No inline preview for this file type."
        return {
            "path": str(target),
            "name": target.name or str(target),
            "kind": kind,
            "summary": summary,
            "content": content,
            "image_url": image_url,
            "can_preview": can_preview,
        }

    def _default_workspace_preview(self) -> dict[str, Any]:
        return {
            "path": "",
            "name": "",
            "kind": "",
            "summary": "Select a file to preview.",
            "content": "",
            "image_url": "",
            "can_preview": False,
        }

    def _remember_workspace_change(self, path: str) -> None:
        target = self._workspace_path(path)
        if target is None:
            return
        kind = _workspace_kind(target)
        entry = {
            "path": str(target),
            "name": target.name or str(target),
            "depth": 0,
            "is_dir": target.is_dir(),
            "has_children": False,
            "expanded": False,
            "selected": False,
            "kind": kind,
            "summary": _workspace_summary(target, kind),
        }
        for idx, existing in enumerate(self._workspace_changes):
            if existing.get("path") == entry["path"]:
                del self._workspace_changes[idx]
                break
        self._workspace_changes.insert(0, entry)
        if len(self._workspace_changes) > _WORKSPACE_RECENT_LIMIT:
            del self._workspace_changes[_WORKSPACE_RECENT_LIMIT:]
        if not self._workspace_selected_path:
            self._workspace_selected_path = str(target)
            self._workspace_preview = self._build_workspace_preview(target)
        elif _path_key(self._workspace_selected_path) == _path_key(target):
            self._workspace_preview = self._build_workspace_preview(target)
        self._refresh_workspace_state(emit=False)

    def _snapshot_workspace_text_files(self) -> dict[str, str]:
        root = self.kernel.config.workspace_dir
        try:
            root = root.resolve()
        except OSError:
            root = root.absolute()
        if not root.exists():
            return {}
        snapshot: dict[str, str] = {}
        total_bytes = 0

        def visit(directory: Path) -> None:
            nonlocal total_bytes
            if len(snapshot) >= _WORKSPACE_DIFF_MAX_FILES or total_bytes >= _WORKSPACE_DIFF_MAX_TOTAL_BYTES:
                return
            try:
                children = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
            except OSError:
                return
            for child in children:
                if len(snapshot) >= _WORKSPACE_DIFF_MAX_FILES or total_bytes >= _WORKSPACE_DIFF_MAX_TOTAL_BYTES:
                    return
                if child.is_dir():
                    if child.name in _IGNORED_WORKSPACE_DIRS:
                        continue
                    visit(child)
                    continue
                if _workspace_kind(child) != "text":
                    continue
                try:
                    size = child.stat().st_size
                except OSError:
                    continue
                if size > _WORKSPACE_DIFF_MAX_FILE_BYTES:
                    continue
                if total_bytes + size > _WORKSPACE_DIFF_MAX_TOTAL_BYTES:
                    continue
                try:
                    snapshot[str(child.resolve())] = child.read_text(encoding="utf-8", errors="replace")
                    total_bytes += size
                except OSError:
                    continue

        visit(root)
        return snapshot

    def _emit_file_diff(self, session_id: str, task_id: str, path: str, diff_text: str, turn: int = 0) -> None:
        if not diff_text.strip():
            return
        task = self._running_tasks.get(session_id)
        key = _path_key(path)
        if task is not None:
            task.emitted_diffs[key] = path
        self._remember_workspace_change(path)
        self._emit_event(
            "file_diff",
            session_id=session_id,
            task_id=task_id,
            path=path,
            diff=diff_text,
            turn=turn,
        )
        self._emit_event("workspace_changed", snapshot=self._snapshot())

    def _clear_file_diff(self, session_id: str, task_id: str, path: str, turn: int = 0) -> None:
        self._emit_event(
            "file_diff",
            session_id=session_id,
            task_id=task_id,
            path=path,
            diff="",
            turn=turn,
            clear=True,
        )

    def _emit_workspace_snapshot_diffs(self, session_id: str, task_id: str) -> None:
        task = self._running_tasks.get(session_id)
        if task is None:
            return
        before = task.workspace_before
        after = self._snapshot_workspace_text_files()
        changed_paths = sorted(set(before) | set(after))
        compared_keys = {_path_key(path) for path in changed_paths}
        final_changed_keys: set[str] = set()
        for path in changed_paths:
            before_text = before.get(path, "")
            after_text = after.get(path, "")
            if before_text == after_text:
                continue
            final_changed_keys.add(_path_key(path))
            diff = difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=f"a/{Path(path).name}",
                tofile=f"b/{Path(path).name}",
                lineterm="",
            )
            diff_text = "\n".join(line.rstrip("\n") for line in diff)
            self._emit_file_diff(session_id, task_id, path, diff_text, task.tool_turn)
        for key, path in list(task.emitted_diffs.items()):
            if key in compared_keys and key not in final_changed_keys:
                self._clear_file_diff(session_id, task_id, path, task.tool_turn)

    def _workspace_path(self, path_or_url: str) -> Path | None:
        raw = str(path_or_url or "").strip()
        if raw.startswith("Diff:"):
            raw = raw[5:].strip()
        if not raw:
            return None
        if raw.startswith("file:///"):
            raw = raw[8:]
        root = self.kernel.config.workspace_dir.resolve()
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            candidate = candidate.resolve()
        except OSError:
            candidate = candidate.absolute()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    def select_workspace_path(self, path: str) -> bool:
        target = self._workspace_path(path)
        if target is None:
            return False
        if target.is_dir():
            key = _path_key(target)
            if key in self._workspace_expanded:
                self._workspace_expanded.remove(key)
            else:
                self._workspace_expanded.add(key)
            self._refresh_workspace_state()
            return True
        self._workspace_selected_path = str(target)
        self._expand_ancestors(target)
        self._workspace_preview = self._build_workspace_preview(target)
        self._refresh_workspace_state()
        return True

    def attach_workspace_path(self, path: str) -> bool:
        target = self._workspace_path(path)
        if target is None or not target.is_file():
            return False
        return self.add_attachment(str(target))

    def add_attachment(self, path_or_url: str) -> bool:
        if len(self._attachments) >= _MAX_ATTACHMENTS:
            return False
        path = self._attachment_path(path_or_url)
        if path is None or not path.is_file():
            return False
        attachment = self._make_attachment(path)
        if any(item["path"] == attachment["path"] for item in self._attachments):
            return False
        self._attachments.append(attachment)
        return True

    def remove_attachment(self, row: int) -> bool:
        if row < 0 or row >= len(self._attachments):
            return False
        del self._attachments[row]
        return True

    def clear_attachments(self) -> None:
        self._attachments.clear()

    def _attachment_path(self, path_or_url: str) -> Path | None:
        raw = str(path_or_url or "").strip()
        if not raw:
            return None
        if raw.startswith("file:///"):
            raw = raw[8:]
        try:
            return Path(raw).expanduser().resolve()
        except OSError:
            return None

    def _make_attachment(self, path: Path) -> dict[str, Any]:
        kind = _attachment_kind(path)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return {
            "path": str(path),
            "name": path.name,
            "kind": kind,
            "summary": f"{kind} file, {_fmt_bytes(size)}",
        }

    def _compose_task_with_attachments(self, task: str, attachments: list[dict[str, Any]]) -> str:
        task = task.strip()
        if not attachments:
            return task
        if not task:
            task = "请处理我附加的文件。"
        sections = [task, "", "[ATTACHMENTS]"]
        for index, attachment in enumerate(attachments, 1):
            sections.append(f"{index}. {attachment['name']}")
            sections.append(f"   path: {attachment['path']}")
            sections.append(f"   type: {attachment['kind']}")
            sections.append(f"   summary: {attachment['summary']}")
            preview = self._attachment_preview(attachment)
            if preview:
                sections.append("   preview:")
                sections.append(self._indent(preview, "     "))
        sections.append("[/ATTACHMENTS]")
        return "\n".join(sections).strip()

    def _compose_display_task(self, task: str, attachments: list[dict[str, Any]]) -> str:
        task = task.strip()
        if not attachments:
            return task
        lines = [task or "请处理我附加的文件。", "", "Attachments:"]
        for attachment in attachments:
            lines.append(f"- {attachment['name']} ({attachment['kind']})")
        return "\n".join(lines).strip()

    def _attachment_preview(self, attachment: dict[str, Any]) -> str:
        if attachment.get("kind") != "text":
            return ""
        path = Path(str(attachment.get("path") or ""))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        if len(text) <= _ATTACHMENT_PREVIEW_CHARS:
            return text.strip()
        return text[:_ATTACHMENT_PREVIEW_CHARS].rstrip() + "\n...[preview truncated]"

    def _indent(self, text: str, prefix: str) -> str:
        return "\n".join(prefix + line for line in text.splitlines())

