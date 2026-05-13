"""分层记忆管理器。

Chrysalis 使用接近 GA 的 L0-L4 分层：
- L0: 规则/SOP，例如 memory_management_sop.md
- L1: 极简索引 global_mem_insight.txt
- L2: 稳定事实 global_mem.txt
- L3: 技能/SOP 文件，位于 memory/
- L4: 会话归档 data/l4_sessions.jsonl

模型不应该直接随意写这些文件；后续长期记忆工具都应通过这里收口。
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from chrysalis.text import brief_text


SECRET_PATTERNS = (
    re.compile(r"api[_-]?key\s*[:=]", re.IGNORECASE),
    re.compile(r"secret\s*[:=]", re.IGNORECASE),
    re.compile(r"password\s*[:=]", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
)


@dataclass(frozen=True)
class MemoryPaths:
    l0_meta_sop: Path
    l1_insight: Path
    l2_global: Path
    l3_dir: Path
    l4_archive: Path
    machine_memory: Path


class MemoryManager:
    def __init__(self, memory_dir: Path, data_path: Path):
        self.memory_dir = memory_dir
        self.data_path = data_path
        self.paths = MemoryPaths(
            l0_meta_sop=memory_dir / "memory_management_sop.md",
            l1_insight=memory_dir / "global_mem_insight.txt",
            l2_global=memory_dir / "global_mem.txt",
            l3_dir=memory_dir,
            l4_archive=data_path.parent / "l4_sessions.jsonl",
            machine_memory=data_path,
        )
        self.ensure_structure()

    def ensure_structure(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.paths.l1_insight.exists():
            self.paths.l1_insight.write_text("# Chrysalis 记忆\n\n## 技能\n\n", encoding="utf-8")
        if not self.paths.l2_global.exists():
            self.paths.l2_global.write_text(
                "# Chrysalis 全局事实\n\n"
                "当前为空。只记录经过工具验证、跨会话仍重要的环境事实。\n",
                encoding="utf-8",
            )
        if not self.paths.l0_meta_sop.exists():
            self.paths.l0_meta_sop.write_text(
                "# 记忆管理 SOP\n\n"
                "- No Execution, No Memory：没有工具验证的信息不要写入长期记忆。\n"
                "- L1 只写索引，L2 写稳定事实，L3 写技能/SOP，L4 写会话归档。\n",
                encoding="utf-8",
            )
        self._ensure_machine_memory()
        self.paths.l4_archive.touch(exist_ok=True)

    def l1_context(self) -> str:
        return self.paths.l1_insight.read_text(encoding="utf-8")

    def remember_episode(self, task: str, result: str) -> None:
        data = self._read_machine_memory()
        data.setdefault("episodes", []).append({
            "time": datetime.now(timezone.utc).isoformat(),
            "task": task,
            "result": brief_text(result),
        })
        data["episodes"] = data["episodes"][-50:]
        self.paths.machine_memory.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.archive_session(task, result)

    def archive_session(self, task: str, result: str) -> None:
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "task": brief_text(task, 800),
            "result": brief_text(result, 1200),
        }
        with self.paths.l4_archive.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def add_skill_index(self, name: str, description: str) -> None:
        self._validate_memory_text(name)
        self._validate_memory_text(description)
        text = self.paths.l1_insight.read_text(encoding="utf-8")
        lines = text.splitlines()
        prefix = f"- {name}:"
        new_line = f"- {name}: {description}"
        replaced = False
        for index, line in enumerate(lines):
            if line.strip().startswith(prefix):
                lines[index] = new_line
                replaced = True
                break
        if not replaced:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(new_line)
        self.paths.l1_insight.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def list_skills(self) -> list[dict]:
        skills = []
        for line in self.paths.l1_insight.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith("- ") or ":" not in stripped:
                continue
            name, description = stripped[2:].split(":", 1)
            name = name.strip()
            if name:
                skills.append({"name": name, "description": description.strip()})
        return skills

    def add_global_fact(self, section: str, fact: str, evidence: str) -> None:
        section = section.strip() or "GENERAL"
        fact = fact.strip()
        evidence = evidence.strip()
        if not fact:
            raise ValueError("长期事实不能为空")
        if not evidence:
            raise ValueError("写入 L2 必须提供工具验证依据")
        self._validate_memory_text(fact)
        self._validate_memory_text(evidence)
        text = self.paths.l2_global.read_text(encoding="utf-8")
        entry = f"- {fact}（依据：{evidence}）"
        if entry in text:
            return
        header = f"## {section}"
        if header not in text:
            text = text.rstrip() + f"\n\n{header}\n"
        text = text.rstrip() + f"\n{entry}\n"
        self.paths.l2_global.write_text(text, encoding="utf-8")

    def _ensure_machine_memory(self) -> None:
        if not self.paths.machine_memory.exists() or not self.paths.machine_memory.read_text(encoding="utf-8").strip():
            self.paths.machine_memory.write_text(json.dumps({"episodes": []}, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        try:
            json.loads(self.paths.machine_memory.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self.paths.machine_memory.write_text(json.dumps({"episodes": []}, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_machine_memory(self) -> dict:
        self._ensure_machine_memory()
        data = json.loads(self.paths.machine_memory.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"episodes": []}

    def _validate_memory_text(self, text: str) -> None:
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                raise ValueError("拒绝把疑似密钥或密码写入长期记忆")
