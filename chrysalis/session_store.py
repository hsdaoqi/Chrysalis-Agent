"""会话持久化存储。

每个会话对应 data/sessions/{id}.json，内容是 LLM 的 canonical history。
每轮交互后整体重写，压缩后也同步更新。
"""

import json
import os
import random
import string
import time
from datetime import datetime
from pathlib import Path
from typing import Callable


def _generate_id() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{ts}_{suffix}"


def _extract_title(history: list[dict], max_len: int = 40) -> str:
    for msg in history:
        if msg.get("role") != "user":
            continue
        for block in msg.get("blocks", []):
            if block.get("type") == "text":
                text = block.get("text", "").strip()
                if text:
                    text = text.replace("\n", " ")
                    return text[:max_len] if len(text) > max_len else text
    return "Untitled"


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

        data = {
            "id": self._current_id,
            "title": _extract_title(history),
            "created_at": self._get_created_at(),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "model": self._model,
            "turns": len(history),
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
            key=lambda f: f.stat().st_mtime,
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
                    "turns": data.get("turns", 0),
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

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def _get_created_at(self) -> str:
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
