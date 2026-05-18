"""任务队列：从文件读取待处理任务，交互模式空闲时自动执行。"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class QueuedTask:
    task: str
    status: str = "pending"
    result: str = ""
    created_at: str = ""
    finished_at: str = ""


class TaskQueue:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def save(self, tasks: list[dict]) -> None:
        self.path.write_text(
            json.dumps(tasks, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def next_pending(self) -> tuple[int, dict] | None:
        tasks = self.load()
        for i, t in enumerate(tasks):
            if t.get("status") == "pending":
                return i, t
        return None

    def mark_running(self, index: int) -> None:
        tasks = self.load()
        if index < len(tasks):
            tasks[index]["status"] = "running"
            self.save(tasks)

    def mark_done(self, index: int, result: str) -> None:
        tasks = self.load()
        if index < len(tasks):
            tasks[index]["status"] = "done"
            tasks[index]["result"] = result[:500]
            tasks[index]["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.save(tasks)

    def mark_failed(self, index: int, error: str) -> None:
        tasks = self.load()
        if index < len(tasks):
            tasks[index]["status"] = "failed"
            tasks[index]["result"] = error[:500]
            tasks[index]["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.save(tasks)

    def add(self, task: str) -> None:
        tasks = self.load()
        tasks.append({
            "task": task,
            "status": "pending",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self.save(tasks)

    def pending_count(self) -> int:
        return sum(1 for t in self.load() if t.get("status") == "pending")
