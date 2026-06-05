"""会话持久化存储。

每个会话对应 data/sessions/{id}.json，内容是 LLM 的 canonical history。
每轮交互后整体重写，压缩后也同步更新。
"""

import json
import os
import random
import string
from datetime import datetime
from pathlib import Path

from chrysalis.history_display import has_tool_result, message_blocks, visible_user_text


def _generate_id() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{ts}_{suffix}"


def _extract_title(history: list[dict], max_len: int = 40) -> str:
    """从第一条 user text block 生成默认标题。"""
    for msg in history:
        if msg.get("role") != "user":
            continue
        text = visible_user_text(msg)
        if text:
            text = text.replace("\n", " ")
            return text[:max_len] if len(text) > max_len else text
    return "Untitled"


def _count_user_turns(history: list[dict]) -> int:
    turns = 0
    for msg in history:
        if msg.get("role") != "user":
            continue
        blocks = message_blocks(msg)
        if has_tool_result(blocks):
            continue
        text = visible_user_text(msg)
        if text:
            turns += 1
    return turns


def _session_sort_key(path: Path) -> tuple[int, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return (0, path.stat().st_mtime if path.exists() else 0)
    return (1 if data.get("pinned") else 0, path.stat().st_mtime if path.exists() else 0)


class SessionStore:
    """会话文件管理。一个 SessionStore 实例对应一个 sessions 目录。"""

    def __init__(self, sessions_dir: Path):
        self.sessions_dir = sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._current_id: str | None = None
        self._model: str = ""

    @property
    def current_id(self) -> str | None:
        return self._current_id

    def new_session(self, model: str = "") -> str:
        session_id = _generate_id()
        self._current_id = session_id
        self._model = model
        return session_id

    def save(self, history: list[dict]) -> None:
        if not self._current_id:
            self.new_session(self._model)
        existing = self._load_metadata(self._current_id)

        data = {
            "id": self._current_id,
            "title": existing.get("custom_title") or _extract_title(history),
            "custom_title": existing.get("custom_title", ""),
            "pinned": bool(existing.get("pinned", False)),
            "created_at": self._get_created_at(),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "model": self._model,
            "turns": _count_user_turns(history),
            "history": history,
        }

        path = self._session_path(self._current_id)
        tmp_path = path.with_suffix(".tmp")
        try:
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            os.replace(tmp_path, path)
        except OSError:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def load(self, session_id: str) -> list[dict]:
        path = self._session_path(session_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        self._current_id = session_id
        self._model = data.get("model", "")
        return data.get("history", [])

    def list_sessions(self, limit: int = 20) -> list[dict]:
        files = sorted(
            self.sessions_dir.glob("*.json"),
            key=_session_sort_key,
            reverse=True,
        )
        results = []
        for f in files[:limit]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results.append({
                    "id": data.get("id", f.stem),
                    "title": data.get("title", "Untitled"),
                    "updated_at": data.get("updated_at", ""),
                    "model": data.get("model", ""),
                    "turns": _count_user_turns(data.get("history", [])) if isinstance(data.get("history"), list) else data.get("turns", 0),
                    "pinned": bool(data.get("pinned", False)),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return results

    def delete(self, session_id: str) -> bool:
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
            if self._current_id == session_id:
                self._current_id = None
            return True
        return False

    def rename(self, session_id: str, title: str) -> bool:
        title = title.strip()
        if not title:
            return False
        data = self._read_session_data(session_id)
        if data is None:
            return False
        data["title"] = title
        data["custom_title"] = title
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            self._write_session_data(session_id, data)
        except OSError:
            return False
        return True

    def set_pinned(self, session_id: str, pinned: bool) -> bool:
        """设置（修改）某个特定会话（Session）的“置顶”或“固定”状态。"""
        data = self._read_session_data(session_id)
        if data is None:
            return False
        data["pinned"] = bool(pinned)
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._write_session_data(session_id, data)
        return True

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def _read_session_data(self, session_id: str) -> dict | None:
        """读取某个会话的数据"""
        path = self._session_path(session_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_session_data(self, session_id: str, data: dict) -> None:
        """将用户的会话数据（Session Data）以 JSON 格式安全、可靠地保存到本地磁盘文件中。"""
        path = self._session_path(session_id)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp_path, path)

    def _load_metadata(self, session_id: str | None) -> dict:
        if not session_id:
            return {}
        return self._read_session_data(session_id) or {}

    def _get_created_at(self) -> str:
        """获取创建时间"""
        if not self._current_id:
            return datetime.now().isoformat(timespec="seconds")
        path = self._session_path(self._current_id)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data.get("created_at", datetime.now().isoformat(timespec="seconds"))
            except (json.JSONDecodeError, OSError):
                pass
        return datetime.now().isoformat(timespec="seconds")
