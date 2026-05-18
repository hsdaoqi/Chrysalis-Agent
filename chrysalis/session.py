"""交互会话内的短期上下文，支持跨 session 持久化。

它和 WorkingMemory 不同：
- WorkingMemory 是单个任务内部的多轮执行状态，每次 run() 开始清空。
- SessionContext 是交互模式下多个用户任务之间的最近上下文，持久化到磁盘。
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from utils.text import brief_text

SESSION_FILE = "session.json"
MAX_AGE_HOURS = 24


@dataclass
class SessionTurn:
    task: str
    final: str
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return {"task": self.task, "final": self.final, "timestamp": self.timestamp}

    @staticmethod
    def from_dict(d: dict) -> "SessionTurn":
        return SessionTurn(
            task=d.get("task", ""),
            final=d.get("final", ""),
            timestamp=d.get("timestamp", 0.0),
        )


@dataclass
class SessionContext:
    max_turns: int = 6
    max_text_chars: int = 500
    turns: list[SessionTurn] = field(default_factory=list)
    persist_path: Path | None = None

    def remember(self, task: str, result: dict) -> None:
        final = result.get("final") or result.get("error") or ""
        self.turns.append(SessionTurn(
            task=brief_text(str(task), self.max_text_chars),
            final=brief_text(str(final), self.max_text_chars),
            timestamp=time.time(),
        ))
        self.turns = self.turns[-self.max_turns:]
        self._save()

    def context(self) -> str:
        if not self.turns:
            return ""
        lines = ["## 本次交互会话的最近上下文"]
        for index, turn in enumerate(self.turns, 1):
            lines.append(f"{index}. 用户任务：{turn.task}")
            lines.append(f"   上次结果：{turn.final}")
        return "\n".join(lines)

    def clear(self) -> None:
        self.turns.clear()
        self._save()

    def load(self) -> None:
        """从磁盘恢复 session 状态。"""
        if not self.persist_path or not self.persist_path.exists():
            return
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        turns = [SessionTurn.from_dict(t) for t in data.get("turns", [])]
        # 过滤掉过期的 turns
        cutoff = time.time() - MAX_AGE_HOURS * 3600
        self.turns = [t for t in turns if t.timestamp > cutoff][-self.max_turns:]

    def _save(self) -> None:
        """持久化到磁盘。"""
        if not self.persist_path:
            return
        data = {"turns": [t.to_dict() for t in self.turns]}
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            self.persist_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
