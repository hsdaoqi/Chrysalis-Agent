"""Task and tool permission decisions for Chrysalis."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from chrysalis.tools.registry import get_registry
from chrysalis.tools.safety import SECRET_NAMES, safe_path

PermissionState = Literal["allow", "ask", "deny"]
PermissionLevel = Literal["locked", "balanced", "full"]

SAFE_TOOLS = {
    "ocr",
    "ask_user",
    "update_working_checkpoint",
    "start_long_term_update",
    "todo_write",
}

ASK_TOOLS = {
    "file_write",
    "file_patch",
    "code_run",
    "web_scan",
    "web_execute_js",
    "screenshot",
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

HIGH_RISK_CODE_PATTERNS = (
    "shutil.rmtree",
    "remove-item",
    "rm -rf",
    "taskkill",
    "shutdown",
    "restart-computer",
    "format",
    "git reset --hard",
)

SENSITIVE_PATH_PARTS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "browser_profile",
    ".npm-cache",
}


@dataclass(frozen=True)
class PermissionOption:
    id: str
    label: str
    value: str
    description: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "value": self.value,
            "description": self.description,
        }


@dataclass(frozen=True)
class PermissionDecision:
    decision: PermissionState
    reason: str
    prompt: str = ""
    tool: str = ""
    risk: str = "low"
    details: dict[str, Any] = field(default_factory=dict)
    grant_key: str = ""
    options: list[PermissionOption] = field(default_factory=list)

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
        if self.grant_key:
            result["grant_key"] = self.grant_key
        if self.needs_user:
            option_dicts = [option.to_dict() for option in self.options] or _default_options()
            result.update({
                "need_user": True,
                "permission_request": True,
                "question": self.prompt or self.reason,
                "final": self.prompt or self.reason,
                "candidates": [option["label"] for option in option_dicts],
                "options": option_dicts,
            })
        return result


@dataclass(frozen=True)
class PermissionRequest:
    kind: str
    tool: str = ""
    risk: str = "low"
    reason: str = ""
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def grant_key(self) -> str:
        payload = {
            "kind": self.kind,
            "tool": self.tool,
            "summary": self.summary,
            "details": self._stable_details(),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @property
    def broad_grant_key(self) -> str:
        payload = self._broad_payload()
        if not payload:
            return ""
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @property
    def broad_summary(self) -> str:
        if self.tool == "code_run":
            code_type = str(self.details.get("code_type", "code")).lower()
            return f"{code_type} *"
        if self.tool == "web_scan":
            return "web_scan *"
        if self.tool == "screenshot":
            return "screenshot *"
        return self.summary

    def _stable_details(self) -> dict[str, Any]:
        stable = {}
        for key in ("path", "mode", "code_type", "cwd", "url", "tab_id", "task"):
            if key in self.details and self.details[key] not in (None, ""):
                stable[key] = self.details[key]
        if self.tool == "code_run":
            script = str(self.details.get("script", ""))
            stable["script_hash"] = hashlib.sha256(script.encode("utf-8")).hexdigest()[:24]
        if self.tool == "web_execute_js":
            script = str(self.details.get("script", ""))
            stable["script_hash"] = hashlib.sha256(script.encode("utf-8")).hexdigest()[:24]
        return stable

    def _broad_payload(self) -> dict[str, Any]:
        if self.tool == "code_run":
            return {
                "kind": self.kind,
                "tool": self.tool,
                "code_type": str(self.details.get("code_type", "python")).lower(),
                "cwd": self.details.get("cwd", ""),
            }
        if self.tool in {"web_scan", "screenshot"}:
            return {"kind": self.kind, "tool": self.tool}
        return {}


class PermissionStore:
    """Persists user-approved permission grants."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._grants: dict[str, dict[str, Any]] = {}
        self._load()

    def is_allowed(self, grant_key: str) -> bool:
        return bool(grant_key and grant_key in self._grants)

    def allow(self, request: PermissionRequest) -> None:
        if not request.grant_key:
            return
        self._grants[request.grant_key] = self._grant_payload(request)
        broad_key = request.broad_grant_key
        if broad_key:
            payload = self._grant_payload(request)
            payload["scope"] = "broad"
            payload["summary"] = request.broad_summary
            self._grants[broad_key] = payload
        self._save()

    def _grant_payload(self, request: PermissionRequest) -> dict[str, Any]:
        return {
            "kind": request.kind,
            "tool": request.tool,
            "risk": request.risk,
            "summary": request.summary,
            "reason": request.reason,
            "details": request._stable_details(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def list_grants(self) -> list[dict[str, Any]]:
        return list(self._grants.values())

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        grants = data.get("grants") if isinstance(data, dict) else None
        if isinstance(grants, dict):
            self._grants = grants

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "grants": self._grants}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class PermissionEngine:
    """Local permission gate with three access levels and persistent grants."""

    def __init__(
        self,
        level: str = "balanced",
        store_path: Path | None = None,
        ask_on_task_mutation: bool | None = None,
        ask_on_tool_mutation: bool | None = None,
    ) -> None:
        self.level = _normalize_level(level)
        self.store = PermissionStore(store_path)
        self.ask_on_task_mutation = ask_on_task_mutation
        self.ask_on_tool_mutation = ask_on_tool_mutation
        self._pending: dict[str, PermissionRequest] = {}
        self._one_time_grants: set[str] = set()

    def assess_task(self, task: str, session_context: str = "") -> PermissionDecision:
        if self.level == "full":
            return PermissionDecision("allow", "full access mode" if self.level == "full" else "user approved")

        lowered = task.lower()
        for pattern in DANGEROUS_TASK_PATTERNS:
            if re.search(pattern, lowered, flags=re.IGNORECASE):
                return PermissionDecision(
                    "deny",
                    "task requests a destructive system action",
                    risk="high",
                    details={"pattern": pattern},
                )

        should_ask = self.ask_on_task_mutation
        if should_ask is None:
            should_ask = self.level == "locked"
        if should_ask and self._looks_mutating_task(lowered):
            request = PermissionRequest(
                kind="task",
                risk="medium",
                reason="task may modify local state",
                summary=task,
                details={"task": task},
            )
            return self._ask(request, f"允许 Chrysalis 继续处理这个任务吗？\n{task}")

        return PermissionDecision("allow", "task is within the current capability set")

    def assess_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        workspace: Path | None = None,
        session_context: str = "",
    ) -> PermissionDecision:
        if self.level == "full":
            return PermissionDecision("allow", "full access mode", tool=tool_name)

        if tool_name not in get_registry():
            return PermissionDecision("deny", f"unknown tool: {tool_name}", tool=tool_name, risk="high")

        request = self._request_for_tool(tool_name, args, workspace)
        if request is None:
            return PermissionDecision("allow", "read-only or internal control tool", tool=tool_name)

        if self.store.is_allowed(request.grant_key):
            return PermissionDecision(
                "allow",
                "matched a persistent permission grant",
                tool=tool_name,
                risk=request.risk,
                details=request.details,
                grant_key=request.grant_key,
            )
        if request.broad_grant_key and self.store.is_allowed(request.broad_grant_key):
            return PermissionDecision(
                "allow",
                "matched a broad persistent permission grant",
                tool=tool_name,
                risk=request.risk,
                details=request.details,
                grant_key=request.broad_grant_key,
            )
        if request.grant_key in self._one_time_grants:
            self._one_time_grants.remove(request.grant_key)
            return PermissionDecision(
                "allow",
                "matched a one-time permission grant",
                tool=tool_name,
                risk=request.risk,
                details=request.details,
                grant_key=request.grant_key,
            )

        should_ask = self.ask_on_tool_mutation
        if should_ask is None:
            should_ask = self.level in {"locked", "balanced"}
        if not should_ask:
            return PermissionDecision("allow", request.reason, tool=tool_name, risk=request.risk, details=request.details)

        if self.level == "locked" and request.risk == "high":
            return self._ask(request, self._prompt_for_request(request))

        return self._ask(request, self._prompt_for_request(request))

    def resolve_user_choice(self, result: dict[str, Any], choice: str) -> dict[str, Any]:
        action = _choice_action(result, choice)
        grant_key = str(result.get("grant_key", ""))
        request = self._pending.pop(grant_key, None) if grant_key else None

        if action == "allow_always" and request is not None:
            self.store.allow(request)
            return {"action": "allow", "context": _approval_context(request, persistent=True)}
        if action == "allow_once":
            if request is not None:
                self._one_time_grants.add(request.grant_key)
            return {"action": "allow", "context": _approval_context(request, persistent=False)}
        if action == "deny":
            return {"action": "deny", "context": "用户拒绝了本次权限请求。"}
        if action == "detail":
            return {"action": "detail", "context": _details_text(result)}
        return {"action": "custom", "context": choice}

    def _request_for_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        workspace: Path | None,
    ) -> PermissionRequest | None:
        if tool_name in SAFE_TOOLS:
            return None

        if tool_name == "file_read":
            raw_path = str(args.get("path", ""))
            target = self._resolve_target(str(args.get("path", "")), workspace)
            if target and _is_sensitive_path(target):
                return PermissionRequest(
                    kind="tool",
                    tool=tool_name,
                    risk="high",
                    reason="reading a sensitive local path needs confirmation",
                    summary=f"读取 {target}",
                    details={"path": str(target)},
                )
            if raw_path and target is None and _looks_secret_name(raw_path):
                return PermissionRequest(
                    kind="tool",
                    tool=tool_name,
                    risk="high",
                    reason="reading a sensitive local path needs confirmation",
                    summary=f"读取 {raw_path}",
                    details={"path": raw_path},
                )
            return None

        if tool_name == "code_run":
            script = str(args.get("script", args.get("code", "")))
            blocked = self._blocked_code(script)
            code_type = str(args.get("type", "python"))
            cwd = str(args.get("cwd", ""))
            risk = "high" if blocked or code_type.lower() != "python" else "medium"
            reason = f"code execution needs confirmation"
            if blocked:
                reason = f"code contains a high-risk pattern: {blocked}"
            return PermissionRequest(
                kind="tool",
                tool=tool_name,
                risk=risk,
                reason=reason,
                summary=f"执行 {code_type} 代码",
                details={
                    "code_type": code_type,
                    "cwd": cwd,
                    "script": script,
                    "preview": _truncate(script, 600),
                    "pattern": blocked,
                },
            )

        if tool_name in {"file_write", "file_patch"}:
            target = self._resolve_target(str(args.get("path", "")), workspace)
            path_text = str(target) if target else str(args.get("path", ""))
            risk = "high" if target and _is_sensitive_path(target) else "medium"
            return PermissionRequest(
                kind="tool",
                tool=tool_name,
                risk=risk,
                reason="file mutation needs confirmation",
                summary=f"修改文件 {path_text}",
                details={
                    "path": path_text,
                    "mode": str(args.get("mode", "")),
                    "preview": _preview_args(args),
                },
            )

        if tool_name == "web_scan":
            url = str(args.get("url", ""))
            return PermissionRequest(
                kind="tool",
                tool=tool_name,
                risk="low",
                reason="browser navigation may send local browser state to a site",
                summary=f"打开或扫描网页 {url or '(current tab)'}",
                details={"url": url, "tab_id": str(args.get("tab_id", ""))},
            )

        if tool_name == "web_execute_js":
            script = str(args.get("script", ""))
            return PermissionRequest(
                kind="tool",
                tool=tool_name,
                risk="medium",
                reason="browser JavaScript execution needs confirmation",
                summary="在浏览器页面执行 JavaScript",
                details={
                    "tab_id": str(args.get("tab_id", "")),
                    "script": script,
                    "preview": _truncate(script, 600),
                },
            )

        if tool_name == "screenshot":
            return PermissionRequest(
                kind="tool",
                tool=tool_name,
                risk="medium",
                reason="screen capture may include private information",
                summary="截取当前屏幕",
                details={"monitor": str(args.get("monitor", 1))},
            )

        if tool_name == "spawn_subagent":
            return PermissionRequest(
                kind="tool",
                tool=tool_name,
                risk="medium",
                reason="subagent execution can run additional tool loops",
                summary="派生子 agent 执行任务",
                details={"preview": _preview_args(args)},
            )

        if tool_name in ASK_TOOLS:
            return PermissionRequest(
                kind="tool",
                tool=tool_name,
                risk="medium",
                reason="side-effecting tool needs confirmation",
                summary=f"执行工具 {tool_name}",
                details={"preview": _preview_args(args)},
            )

        return None

    def _ask(self, request: PermissionRequest, prompt: str) -> PermissionDecision:
        self._pending[request.grant_key] = request
        return PermissionDecision(
            "ask",
            request.reason,
            prompt=prompt,
            tool=request.tool,
            risk=request.risk,
            details=request.details,
            grant_key=request.grant_key,
            options=[PermissionOption(**option) for option in _default_options(request.summary, request.broad_summary)],
        )

    def _prompt_for_request(self, request: PermissionRequest) -> str:
        return f"需要确认权限：{request.summary}"

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
                "delete",
                "修改",
                "编辑",
                "运行",
                "执行",
                "删除",
            )
        )

    def _blocked_code(self, code: str) -> str:
        lowered = code.lower()
        for pattern in HIGH_RISK_CODE_PATTERNS:
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

    def _is_confirmed(self, session_context: str) -> bool:
        lowered = session_context.lower()
        return "permission approved:" in lowered or "user approved" in lowered


class FullAccessPermissionEngine(PermissionEngine):
    """Permission engine for trusted local runs."""

    def __init__(self) -> None:
        super().__init__(level="full")

    def assess_task(self, task: str, session_context: str = "") -> PermissionDecision:
        return PermissionDecision("allow", "full access mode")

    def assess_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        workspace: Path | None = None,
        session_context: str = "",
    ) -> PermissionDecision:
        return PermissionDecision("allow", "full access mode", tool=tool_name)


def _normalize_level(value: str) -> PermissionLevel:
    normalized = str(value or "balanced").strip().lower()
    aliases = {
        "strict": "locked",
        "safe": "locked",
        "ask": "locked",
        "normal": "balanced",
        "default": "balanced",
        "trusted": "full",
        "off": "full",
        "none": "full",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"locked", "balanced", "full"}:
        return "balanced"
    return normalized  # type: ignore[return-value]


def _default_options(summary: str = "", broad_summary: str = "") -> list[dict[str, str]]:
    suffix = f"：{_short_option_summary(summary)}" if summary else ""
    broad_suffix = f"：{_short_option_summary(broad_summary or summary)}" if (broad_summary or summary) else ""
    return [
        {"id": "allow_once", "label": f"允许本次{suffix}", "value": "allow_once", "description": ""},
        {"id": "allow_always", "label": f"永久允许{broad_suffix}", "value": "allow_always", "description": ""},
        {"id": "deny", "label": "拒绝", "value": "deny", "description": ""},
        {"id": "detail", "label": "详细说明", "value": "detail", "description": ""},
    ]


def _choice_action(result: dict[str, Any], choice: str) -> str:
    normalized = str(choice).strip().lower()
    for option in result.get("options") or []:
        label = str(option.get("label", "")).strip().lower()
        value = str(option.get("value", "")).strip().lower()
        if normalized in {label, value}:
            return value
    if normalized.startswith("允许本次") or normalized in {"y", "yes", "继续", "允许"}:
        return "allow_once"
    if normalized.startswith("永久允许") or normalized in {"always"}:
        return "allow_always"
    if normalized in {"n", "no", "拒绝", "否"}:
        return "deny"
    if normalized in {"detail", "details", "详细", "详细说明"}:
        return "detail"
    return "custom"


def _approval_context(request: PermissionRequest | None, persistent: bool) -> str:
    scope = "persistent" if persistent else "once"
    if request is None:
        return f"Permission approved: {scope}."
    return (
        f"Permission approved: {scope}. "
        f"tool={request.tool or request.kind}; risk={request.risk}; "
        f"summary={request.summary}; grant_key={request.grant_key}."
    )


def _details_text(result: dict[str, Any]) -> str:
    details = result.get("details") or {}
    return (
        "这次操作还没有执行。\n\n"
        f"原因：{result.get('reason', '')}\n"
        f"工具：{result.get('tool', '') or 'task'}\n"
        f"风险：{_risk_label(str(result.get('risk', 'low')))}\n"
        f"参数：\n{json.dumps(details, ensure_ascii=False, indent=2, default=str)}"
    )


def _risk_label(risk: str) -> str:
    return {"low": "低", "medium": "中", "high": "高"}.get(risk, risk)


def _short_option_summary(summary: str, limit: int = 42) -> str:
    text = " ".join(str(summary).split())
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"


def _is_sensitive_path(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    if path.name in SECRET_NAMES or path.suffix.lower() in {".pem", ".key"}:
        return True
    return bool(parts & SENSITIVE_PATH_PARTS)


def _looks_secret_name(path: str) -> bool:
    candidate = Path(path)
    return candidate.name in SECRET_NAMES or candidate.suffix.lower() in {".pem", ".key"}


def _preview_args(args: dict[str, Any]) -> dict[str, Any]:
    preview = {k: v for k, v in args.items() if not str(k).startswith("_")}
    for key, value in list(preview.items()):
        if isinstance(value, str):
            preview[key] = _truncate(value, 500)
    return preview


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"
