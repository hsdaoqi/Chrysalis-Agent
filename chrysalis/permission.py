"""Task and tool permission decisions for Chrysalis."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from chrysalis.tools.registry import get_registry
from chrysalis.tools.safety import safe_path

PermissionState = Literal["allow", "ask", "deny"]

SAFE_TOOLS = {
    "file_read",
    "web_scan",
    "ocr",
    "ask_user",
    "update_working_checkpoint",
    "start_long_term_update",
}

MUTATING_TOOLS = {
    "file_write",
    "file_patch",
    "code_run",
    "web_execute_js",
    "spawn_subagent",
}

DANGEROUS_TASK_PATTERNS = (
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bformat\b",
    r"\bwipe\b",
    r"\bkill\s+-9\b",
    r"\btaskkill\b",
    r"\bremove-item\b",
    r"\brm\s+-rf\b",
    r"\bdel\s+/s\b",
    r"清空.*(磁盘|系统|注册表)",
    r"删除.*(系统|注册表|启动项)",
)


@dataclass(frozen=True)
class PermissionDecision:
    decision: PermissionState
    reason: str
    prompt: str = ""
    tool: str = ""
    risk: str = "low"
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_user(self) -> bool:
        return self.decision == "ask"

    @property
    def denied(self) -> bool:
        return self.decision == "deny"

    def to_result(self) -> dict[str, Any]:
        result = {
            "ok": self.decision == "allow",
            "decision": self.decision,
            "reason": self.reason,
            "prompt": self.prompt,
            "tool": self.tool,
            "risk": self.risk,
            "details": self.details,
        }
        if self.needs_user:
            result.update({
                "need_user": True,
                "question": self.prompt or self.reason,
                "final": self.prompt or self.reason,
                "candidates": ["继续", "跳过"],
            })
        return result


class PermissionEngine:
    """Local heuristic permission gate."""

    def __init__(self, ask_on_task_mutation: bool = False, ask_on_tool_mutation: bool = True) -> None:
        self.ask_on_task_mutation = ask_on_task_mutation
        self.ask_on_tool_mutation = ask_on_tool_mutation

    def assess_task(self, task: str, session_context: str = "") -> PermissionDecision:
        if self._is_confirmed(session_context):
            return PermissionDecision("allow", "user already confirmed the pending action")

        lowered = task.lower()
        for pattern in DANGEROUS_TASK_PATTERNS:
            if re.search(pattern, lowered, flags=re.IGNORECASE):
                return PermissionDecision(
                    "deny",
                    "task requests a destructive system action",
                    risk="high",
                    details={"pattern": pattern},
                )

        if self.ask_on_task_mutation and self._looks_mutating_task(lowered):
            return PermissionDecision(
                "ask",
                "task may modify files or execute code",
                prompt=f"允许 Chrysalis 继续处理这个任务吗？\n{task}",
                risk="medium",
            )

        return PermissionDecision("allow", "task is within the current capability set")

    def assess_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        workspace: Path | None = None,
        session_context: str = "",
    ) -> PermissionDecision:
        if self._is_confirmed(session_context):
            return PermissionDecision("allow", "user already confirmed the pending action", tool=tool_name)

        if tool_name not in get_registry():
            return PermissionDecision("deny", f"unknown tool: {tool_name}", tool=tool_name, risk="high")

        if tool_name in SAFE_TOOLS:
            return PermissionDecision("allow", "read-only or internal control tool", tool=tool_name)

        if tool_name == "code_run":
            code = str(args.get("script", args.get("code", "")))
            blocked = self._blocked_code(code)
            if blocked:
                return PermissionDecision(
                    "deny",
                    f"code contains a blocked pattern: {blocked}",
                    tool=tool_name,
                    risk="high",
                    details={"pattern": blocked},
                )
            return self._ask_or_allow(tool_name, args, "code execution needs explicit confirmation", "high")

        if tool_name in {"file_write", "file_patch"}:
            target = self._resolve_target(str(args.get("path", "")), workspace)
            details = {"path": str(target) if target else str(args.get("path", ""))}
            return self._ask_or_allow(
                tool_name,
                args,
                "file mutation needs explicit confirmation",
                "medium",
                details,
            )

        if tool_name in {"web_execute_js", "spawn_subagent"}:
            return self._ask_or_allow(tool_name, args, "side-effecting tool needs explicit confirmation", "medium")

        return PermissionDecision("allow", "tool is permitted", tool=tool_name)

    def _ask_or_allow(
        self,
        tool_name: str,
        args: dict[str, Any],
        reason: str,
        risk: str,
        details: dict[str, Any] | None = None,
    ) -> PermissionDecision:
        if not self.ask_on_tool_mutation:
            return PermissionDecision("allow", reason, tool=tool_name, risk=risk, details=details or {})
        return PermissionDecision(
            "ask",
            reason,
            prompt=f"允许执行工具 {tool_name} 吗？\n{self._preview_args(args)}",
            tool=tool_name,
            risk=risk,
            details=details or {},
        )

    def _looks_mutating_task(self, lowered_task: str) -> bool:
        return any(
            keyword in lowered_task
            for keyword in (
                "write",
                "edit",
                "modify",
                "change",
                "update",
                "run",
                "execute",
                "修改",
                "编辑",
                "运行",
                "执行",
            )
        )

    def _blocked_code(self, code: str) -> str:
        lowered = code.lower()
        for pattern in (
            "os.popen",
            "shutil.rmtree",
            "socket",
            "subprocess",
            "remove-item",
            "rm -rf",
            "taskkill",
            "shutdown",
        ):
            if pattern in lowered:
                return pattern
        return ""

    def _resolve_target(self, path: str, workspace: Path | None) -> Path | None:
        if not path:
            return None
        try:
            return safe_path(path, workspace)
        except Exception:
            return None

    def _preview_args(self, args: dict[str, Any]) -> dict[str, Any]:
        preview = {k: v for k, v in args.items() if not str(k).startswith("_")}
        for key, value in list(preview.items()):
            if isinstance(value, str) and len(value) > 500:
                preview[key] = value[:500] + "...[truncated]"
        return preview

    def _is_confirmed(self, session_context: str) -> bool:
        lowered = session_context.lower()
        return any(keyword in lowered for keyword in ("user approved", "confirmed", "approved", "允许", "继续"))

