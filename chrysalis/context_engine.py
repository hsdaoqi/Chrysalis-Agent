"""用于组装 Chrysalis 运行时上下文的上下文引擎。

LLM 层仍然负责规范消息历史的运行时压缩（compaction）。
本模块负责更高级别的记忆组装步骤：选择在模型调用前要注入哪些长期（long-term）、工作（working）和会话（session）上下文。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from configs.config import PROJECT_ROOT
from chrysalis.working import WorkingMemory


@dataclass(frozen=True)
class ContextBudget:
    """记忆包（memory bundle）的字符数预算。

    预算有意设计为基于字符进行计算，以保持引擎的无依赖性。
    运行时的 token/窗口裁剪（trimming）逻辑仍保留在 chrysalis.llm.context 中。
    """

    total_chars: int = 12_000
    l1_chars: int = 2_500
    working_chars: int = 1_500
    related_chars: int = 5_000
    session_chars: int = 3_000


@dataclass
class AssembledContext:
    system: str
    anchor: str = ""
    included: list[str] = field(default_factory=list)


class ContextEngine:
    """用于 OpenClaw 风格分层记忆的带预算限制的组装器（assembler）。"""

    def __init__(
            self,
            project_root: Path | None = None,
            memory_dir: Path | None = None,
            budget: ContextBudget | None = None,
    ) -> None:
        self.project_root = project_root or PROJECT_ROOT
        self.memory_dir = memory_dir or self.project_root / "memory"
        self.budget = budget or ContextBudget()

    def assemble(
            self,
            *,
            base_system: str,
            task: str = "",
            working: WorkingMemory | None = None,
            history_lines: list[str] | None = None,
            session_context: str = "",
            include_history_anchor: bool = True,
    ) -> AssembledContext:
        """返回系统提示词（system prompt）以及可选的每轮对话锚点（per-turn anchor）。

        优先级顺序：
        系统提示词 > L1 洞察（insight） > 工作记忆 > 相关的 L2/L3（记忆） > 早期总结 > 近期对话轮次。
        """

        remaining = self.budget.total_chars
        system_parts = [base_system.rstrip()]
        included: list[str] = ["system"]

        memory_parts: list[str] = []
        for name, text, cap in self._memory_sections(task, working, session_context):
            chunk = _clip(text, min(cap, remaining))
            if not chunk:
                continue
            memory_parts.append(chunk)
            included.append(name)
            remaining -= len(chunk)
            if remaining <= 0:
                break

        if memory_parts:
            system_parts.append("\n\n## Context Engine\n" + "\n\n".join(memory_parts))

        anchor = ""
        if include_history_anchor and history_lines:
            anchor = self.session_anchor(history_lines, working, self.budget.session_chars)
            if anchor:
                included.append("session_anchor")

        return AssembledContext(system="\n".join(system_parts), anchor=anchor, included=included)

    def session_anchor(
            self,
            history_lines: list[str],
            working: WorkingMemory | None = None,
            max_chars: int | None = None,
    ) -> str:
        """Build a compact in-session continuity anchor."""

        max_chars = max_chars or self.budget.session_chars
        if not history_lines and not (working and working.snapshot()):
            return ""

        lines: list[str] = ["### [SESSION CONTEXT]"]
        earlier, recent = _split_history(history_lines, recent=30)
        if earlier:
            lines.append("<earlier_summary>")
            lines.append(_fold_earlier(earlier))
            lines.append("</earlier_summary>")
        if recent:
            lines.append("<recent_turns>")
            lines.append("\n".join(recent))
            lines.append("</recent_turns>")
        if working:
            working_prompt = working.to_prompt()
            if working_prompt:
                lines.append(working_prompt)
        return _clip("\n".join(lines), max_chars)

    def _memory_sections(
            self,
            task: str,
            working: WorkingMemory | None,
            session_context: str,
    ) -> list[tuple[str, str, int]]:
        sections: list[tuple[str, str, int]] = []

        l1 = self._read_text(self.memory_dir / "global_mem_insight.txt")
        if l1:
            header = (
                "[Memory L1 Insight]\n"
                "Use this as an index. Read L2/L3 with tools when task details require it.\n"
            )
            sections.append(("l1", header + l1, self.budget.l1_chars))

        if working:
            working_prompt = working.to_prompt()
            if working_prompt:
                sections.append(("working_memory", working_prompt, self.budget.working_chars))

        if session_context.strip():
            sections.append((
                "session_context",
                "[Runtime Continuation]\n" + session_context.strip(),
                min(1_200, self.budget.session_chars),
            ))

        related = self._related_memory(task, working)
        if related:
            sections.append(("related_memory", related, self.budget.related_chars))

        return sections

    def _related_memory(self, task: str, working: WorkingMemory | None) -> str:
        wanted = self._select_related_files(task, working)
        parts: list[str] = []
        for path in wanted:
            text = self._read_text(path)
            if text:
                rel = path.relative_to(self.project_root).as_posix()
                parts.append(f"[{rel}]\n{_clip(text, 2_000)}")
        if not parts:
            return ""
        return "[Relevant L2/L3]\n" + "\n\n".join(parts)

    def _select_related_files(
            self,
            task: str,
            working: WorkingMemory | None,
    ) -> list[Path]:
        query = " ".join([
            task.lower(),
            (working.related_sop.lower() if working and working.related_sop else ""),
        ])
        selected: list[Path] = []

        global_mem = self.memory_dir / "global_mem.txt"
        if global_mem.exists():
            selected.append(global_mem)

        sop_keywords = {
            "git_sop.md": ("git", "commit", "branch", "push", "pull", "merge"),
            "plan_sop.md": ("plan", "规划", "计划", "复杂", "分解"),
            "verify_sop.md": ("verify", "test", "pytest", "验证", "检查"),
            "web_setup_sop.md": ("web", "browser", "网页", "浏览器", "搜索"),
            "tmwebdriver_sop.md": ("webdriver", "上传", "pdf", "iframe", "cookie", "浏览器"),
            "memory_cleanup_sop.md": ("memory", "记忆", "整理", "cleanup"),
            "memory_management_sop.md": ("memory", "记忆", "沉淀", "long term"),
        }
        for filename, keywords in sop_keywords.items():
            explicit = filename.lower() in query
            matched = any(keyword in query for keyword in keywords)
            if explicit or matched:
                path = self.memory_dir / filename
                if path.exists() and path not in selected:
                    selected.append(path)

        if working and working.related_sop:
            for name in working.related_sop.replace(",", " ").split():
                path = self.memory_dir / name
                if path.exists() and path not in selected:
                    selected.append(path)

        return selected[:4]

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace").strip()
        except FileNotFoundError:
            return ""


def _split_history(lines: list[str], recent: int) -> tuple[list[str], list[str]]:
    if len(lines) <= recent:
        return [], lines
    return lines[:-recent], lines[-recent:]


def _fold_earlier(lines: list[str]) -> str:
    parts: list[str] = []
    count = 0
    last = ""

    def flush() -> None:
        nonlocal count, last
        if count:
            parts.append(f"{last or '[Agent]'} ({count} turns)")

    for line in lines:
        if line.startswith("[USER]"):
            flush()
            parts.append(line)
            count = 0
            last = ""
        else:
            count += 1
            last = line
    flush()
    return "\n".join(parts[-120:])


def _clip(text: str, max_chars: int) -> str:
    text = text.strip()
    if max_chars <= 0 or not text:
        return ""
    if len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len("\n...[Truncated]...\n"))
    head = keep // 2
    tail = keep - head
    return text[:head].rstrip() + "\n...[Truncated]...\n" + text[-tail:].lstrip()
