"""用于组装 Chrysalis 运行时上下文的上下文引擎。

LLM 层仍然负责规范消息历史的运行时压缩（compaction）。
本模块负责更高级别的记忆组装步骤：选择在模型调用前要注入哪些长期（long-term）、工作（working）和会话（session）上下文。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from configs.config import PROJECT_ROOT
from chrysalis.working import WorkingMemory

STABLE_CONTEXT_HEADER = "## Stable Context"
RUNTIME_CONTEXT_HEADER = "## Runtime Context"


@dataclass(frozen=True)
class ContextBudget:
    """记忆包（memory bundle）的字符数预算。

    预算有意设计为基于字符进行计算，以保持引擎的无依赖性。
    运行时的 token/窗口裁剪（trimming）逻辑仍保留在 chrysalis.llm.context 中。
    """

    total_chars: int = 12_000
    l1_chars: int = 2_500
    global_chars: int = 2_500
    working_chars: int = 1_500
    related_chars: int = 5_000
    session_chars: int = 3_000


@dataclass
class AssembledContext:
    system: str
    runtime_context: str = ""
    anchor: str = ""
    included: list[str] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextCandidate:
    name: str
    text: str
    budget_chars: int
    stable: bool
    kind: str
    label: str
    source: str = ""
    reason: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)


class ContextEngine:
    """用于 OpenClaw 风格分层记忆的带预算限制的组装器（assembler）。"""

    def __init__(
            self,
            project_root: Path | None = None,
            memory_dir: Path | None = None,
            skills_dir: Path | None = None,
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
            inline_runtime_context: bool = True,
    ) -> AssembledContext:
        """返回系统提示词（system prompt）以及可选的每轮对话锚点（per-turn anchor）。

        优先级顺序：
        系统提示词 > L1 洞察（insight） > 工作记忆 > 相关的 L2/L3（记忆） > 早期总结 > 近期对话轮次。
        """

        remaining = self.budget.total_chars
        system_parts = [base_system.rstrip()]
        included: list[str] = ["system"]
        section_details: list[dict[str, Any]] = []

        stable_parts: list[str] = []
        runtime_parts: list[str] = []
        for section in self._memory_sections(task, working, session_context):
            requested_chars = len(section.text.strip())
            section_budget = min(section.budget_chars, remaining)
            chunk = _clip(section.text, section_budget)
            if not chunk:
                continue
            if section.stable:
                stable_parts.append(chunk)
            else:
                runtime_parts.append(chunk)
            included.append(section.name)
            used_chars = len(chunk)
            section_details.append({
                "name": section.name,
                "label": section.label,
                "kind": section.kind,
                "stable": section.stable,
                "source": section.source,
                "reason": section.reason,
                "budget_chars": section.budget_chars,
                "allocated_chars": section_budget,
                "requested_chars": requested_chars,
                "used_chars": used_chars,
                "truncated": requested_chars > used_chars,
                "items": section.items,
            })
            remaining -= used_chars
            if remaining <= 0:
                break

        if stable_parts:
            system_parts.append("\n\n" + STABLE_CONTEXT_HEADER + "\n" + "\n\n".join(stable_parts))
        runtime_context = ""
        if runtime_parts:
            runtime_context = RUNTIME_CONTEXT_HEADER + "\n" + "\n\n".join(runtime_parts)
            if inline_runtime_context:
                system_parts.append("\n\n" + runtime_context)

        anchor = ""
        if include_history_anchor and history_lines:
            anchor = self.session_anchor(history_lines, working, self.budget.session_chars)
            if anchor:
                included.append("session_anchor")

        used_chars = sum(item["used_chars"] for item in section_details)
        budget = {
            "total_chars": self.budget.total_chars,
            "used_chars": used_chars,
            "remaining_chars": max(0, self.budget.total_chars - used_chars),
            "section_count": len(section_details),
            "sections": section_details,
        }

        return AssembledContext(
            system="\n".join(system_parts),
            runtime_context=runtime_context,
            anchor=anchor,
            included=included,
            budget=budget,
        )

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
    ) -> list[ContextCandidate]:
        sections: list[ContextCandidate] = []

        l1 = self._read_text(self.memory_dir / "global_mem_insight.txt")
        if l1:
            header = (
                "[Memory L1 Insight]\n"
                "Use this as an index. Read L2/L3 with tools when task details require it.\n"
            )
            sections.append(ContextCandidate(
                name="l1",
                label="L1 insight index",
                kind="memory",
                text=header + l1,
                budget_chars=self.budget.l1_chars,
                stable=True,
                source=_rel_or_name(self.memory_dir / "global_mem_insight.txt", self.project_root),
                reason="global_mem_insight.txt exists and is always loaded as the stable memory index",
            ))

        global_memory = self._global_memory()
        if global_memory:
            global_source = _rel_or_name(self.memory_dir / "global_mem.txt", self.project_root)
            global_items = _global_memory_hits(
                self._read_text(self.memory_dir / "global_mem.txt"),
                task=task,
                working=working,
                source=global_source,
            )
            sections.append(ContextCandidate(
                name="global_memory",
                label="Global memory",
                kind="memory",
                text=global_memory,
                budget_chars=self.budget.global_chars,
                stable=True,
                source=global_source,
                reason=(
                    "approved memory blocks matched current task context"
                    if global_items
                    else "global_mem.txt exists and is always loaded as stable project memory"
                ),
                items=global_items,
            ))

        if _should_retrieve_runtime_context(task, working, session_context):
            related, related_items = self._related_memory(task, working)
            if related:
                sections.append(ContextCandidate(
                    name="related_memory",
                    label="Related L2/L3 memory",
                    kind="memory",
                    text=related,
                    budget_chars=self.budget.related_chars,
                    stable=False,
                    reason="task or working memory matched memory file selectors",
                    items=related_items,
                ))

        if working:
            working_prompt = working.to_prompt()
            if working_prompt:
                sections.append(ContextCandidate(
                    name="working_memory",
                    label="Working memory",
                    kind="working",
                    text=working_prompt,
                    budget_chars=self.budget.working_chars,
                    stable=False,
                    reason=_working_reason(working),
                ))
            todo_prompt = working.todo_reminder_prompt()
            if todo_prompt:
                sections.append(ContextCandidate(
                    name="todo_reminder",
                    label="TODO reminder",
                    kind="working",
                    text=todo_prompt,
                    budget_chars=min(1_200, self.budget.working_chars),
                    stable=False,
                    reason="pending TODOs reached the reminder interval",
                ))
            plan_prompt = working.plan_reminder_prompt()
            if plan_prompt:
                sections.append(ContextCandidate(
                    name="plan_reminder",
                    label="Plan reminder",
                    kind="working",
                    text=plan_prompt,
                    budget_chars=min(1_200, self.budget.working_chars),
                    stable=False,
                    reason="active plan reached the reminder interval",
                ))

        if session_context.strip():
            sections.append(ContextCandidate(
                name="session_context",
                label="Runtime continuation",
                kind="session",
                text="[Runtime Continuation]\n" + session_context.strip(),
                budget_chars=min(1_200, self.budget.session_chars),
                stable=False,
                reason="session_context was supplied by the caller",
            ))

        return sections

    def _global_memory(self) -> str:
        text = self._read_text(self.memory_dir / "global_mem.txt")
        if not text:
            return ""
        rel = (self.memory_dir / "global_mem.txt").relative_to(self.project_root).as_posix()
        return "[Global L2 Memory]\n" + f"[{rel}]\n{_clip(text, self.budget.global_chars)}"

    def _related_memory(self, task: str, working: WorkingMemory | None) -> tuple[str, list[dict[str, Any]]]:
        wanted = self._select_related_files(task, working)
        parts: list[str] = []
        items: list[dict[str, Any]] = []
        for path, reason, matched in wanted:
            text = self._read_text(path)
            if text:
                rel = _rel_or_name(path, self.project_root)
                parts.append(f"[{rel}]\n{_clip(text, 2_000)}")
                items.append({
                    "source": rel,
                    "reason": reason,
                    "matched": matched,
                })
        if not parts:
            return "", []
        return "[Relevant L2/L3]\n" + "\n\n".join(parts), items

    def _select_related_files(
            self,
            task: str,
            working: WorkingMemory | None,
    ) -> list[tuple[Path, str, list[str]]]:
        query = " ".join([
            task.lower(),
            (working.related_sop.lower() if working and working.related_sop else ""),
        ])
        selected: list[tuple[Path, str, list[str]]] = []
        selected_paths: set[Path] = set()

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
                if path.exists() and path not in selected_paths:
                    matched_keywords = [keyword for keyword in keywords if keyword in query]
                    reason = "explicit filename match" if explicit else "keyword match"
                    selected.append((path, reason, matched_keywords or [filename]))
                    selected_paths.add(path)

        if working and working.related_sop:
            for name in working.related_sop.replace(",", " ").split():
                path = self.memory_dir / name
                if path.exists() and path not in selected_paths:
                    selected.append((path, "working.related_sop", [name]))
                    selected_paths.add(path)

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


def _rel_or_name(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _working_reason(working: WorkingMemory) -> str:
    reasons: list[str] = []
    if working.key_info:
        reasons.append("key_info")
    if working.related_sop:
        reasons.append("related_sop")
    if working.todo_goal or working.todos:
        reasons.append("todo")
    if working.plan_goal or working.plan_summary:
        reasons.append("plan")
    if working.long_term_update_requested:
        reasons.append("long_term_update")
    if not reasons:
        return "working memory snapshot is non-empty"
    return "working memory fields: " + ", ".join(reasons)


_LIGHT_CHAT_PHRASES = {
    "hi",
    "hello",
    "hey",
    "ok",
    "okay",
    "thanks",
    "thankyou",
    "test",
    "你好",
    "你好啊",
    "你好呀",
    "您好",
    "您好啊",
    "嗨",
    "哈喽",
    "在吗",
    "在么",
    "谢谢",
    "多谢",
    "好的",
    "好",
    "嗯",
    "嗯嗯",
    "测试",
}


def _should_retrieve_runtime_context(
    task: str,
    working: WorkingMemory | None,
    session_context: str,
) -> bool:
    if session_context.strip():
        return True
    if working and working.snapshot():
        return True
    return not _is_light_chat(task)


def _is_light_chat(task: str) -> bool:
    text = str(task or "").strip().lower()
    if not text:
        return True
    compact = re.sub(r"[\s,.;:!?，。！？、~～…]+", "", text)
    if compact in _LIGHT_CHAT_PHRASES:
        return True
    if re.fullmatch(r"(你|您)?好[啊呀哇吗嘛呢]*", compact):
        return True
    if re.fullmatch(r"(嗨|哈喽|hello|hi|hey)+", compact):
        return True
    return False


def _global_memory_hits(
    text: str,
    *,
    task: str,
    working: WorkingMemory | None,
    source: str,
) -> list[dict[str, Any]]:
    query = _context_query(task, working)
    query_tokens = _query_tokens(query)
    if not text.strip() or not query_tokens:
        return []

    hits: list[dict[str, Any]] = []
    for block in _global_memory_blocks(text):
        haystack = block["text"].lower()
        matched = [token for token in query_tokens if token in haystack]
        title = block["title"]
        if not matched and title.lower() not in query:
            continue
        hits.append({
            "name": title,
            "source": source,
            "reason": "approved memory block matched current task tokens",
            "matched": matched[:8] or [title],
        })
    return hits[:5]


def _global_memory_blocks(text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current_title = ""
    current_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current_title or current_lines:
                blocks.append({"title": current_title or "Global memory", "text": "\n".join(current_lines)})
            current_title = line[3:].strip() or "Global memory"
            current_lines = [line]
        elif current_title or current_lines:
            current_lines.append(line)
    if current_title or current_lines:
        blocks.append({"title": current_title or "Global memory", "text": "\n".join(current_lines)})
    return blocks


def _context_query(task: str, working: WorkingMemory | None) -> str:
    parts = [task]
    if working:
        parts.extend([
            working.key_info,
            working.related_sop,
            working.todo_goal,
            working.plan_goal,
            working.plan_summary,
            working.long_term_update_requested,
        ])
    return " ".join(part for part in parts if part).lower()


def _query_tokens(text: str) -> list[str]:
    raw_tokens = re.findall(r"[a-z0-9_+#./:-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "task",
        "user",
        "agent",
        "请",
        "这个",
        "那个",
        "我们",
        "项目",
        "任务",
    }
    tokens: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        token = token.strip(".,;:()[]{}<>\"'`")
        if len(token) < 2 or token in stop or token in seen:
            continue
        tokens.append(token)
        seen.add(token)
    return tokens[:40]


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
