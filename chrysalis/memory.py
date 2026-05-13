"""记忆系统的兼容包装。

新的分层读写规则在 MemoryManager 中；Memory 保留原有调用入口。
"""

from pathlib import Path

from chrysalis.memory_manager import MemoryManager


class Memory:
    def __init__(self, memory_dir: Path, data_path: Path):
        self.manager = MemoryManager(memory_dir, data_path)
        self.memory_dir = memory_dir
        self.data_path = data_path
        self.insight_path = self.manager.paths.l1_insight
        self.global_path = self.manager.paths.l2_global

    def context(self) -> str:
        return self.manager.l1_context()

    def remember_episode(self, task: str, result: str) -> None:
        self.manager.remember_episode(task, result)

    def add_skill(self, name: str, description: str) -> None:
        self.manager.add_skill_index(name, description)

    def list_skills(self) -> list[dict]:
        return self.manager.list_skills()
