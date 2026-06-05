"""Task and tool permission decisions for Chrysalis."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ipaddress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from configs.config import PROJECT_ROOT
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

GATEWAY_SAFE_TOOLS = {
    "ask_user",
    "update_working_checkpoint",
    "start_long_term_update",
    "todo_write",
}

GATEWAY_READ_ONLY_TOOLS = {
    "file_read",
    "ocr",
    "skill_discover",
    "skill_list",
    "skill_search",
    "skill_view",
    "web_fetch",
}

GATEWAY_ALWAYS_DENY_TOOLS = {
    "code_run",
    "file_patch",
    "file_write",
    "gateway_connect",
    "screenshot",
    "skill_archive",
    "skill_create",
    "skill_curate",
    "skill_install",
    "skill_pin",
    "skill_promote",
    "skill_restore",
    "skill_status",
    "spawn_subagent",
    "web_execute_js",
    "web_scan",
}

GATEWAY_DEFAULT_TOOLS = GATEWAY_SAFE_TOOLS | GATEWAY_READ_ONLY_TOOLS

TOOL_ALIASES: dict[str, str] = {}

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
    # r"\bformat\b",
    r"\bwipe\b",
    r"\bkill\s+-9\b",
    r"\btaskkill\b",
    # r"\bremove-item\b",
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
    """UI 上的一个按钮（如“允许本次”）。"""
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
    """引擎给出的最终裁决和给前端的展示数据。"""
    decision: PermissionState
    reason: str
    prompt: str = ""
    tool: str = ""
    risk: str = "low"
    details: dict[str, Any] = field(default_factory=dict)
    grant_key: str = ""
    broad_grant_key: str = ""
    broad_summary: str = ""
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
        if self.broad_grant_key:
            result["broad_grant_key"] = self.broad_grant_key
        if self.broad_summary:
            result["broad_summary"] = self.broad_summary
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
    """将杂乱的工具参数标准化，生成上述提到的哈希指纹（Grant Key）。"""
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
        for key in (
            "path",
            "mode",
            "code_type",
            "cwd",
            "url",
            "tab_id",
            "task",
            "monitor",
            "window_title",
            "window_pid",
            "window_exe",
        ):
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

    def allow_result(self, result: dict[str, Any]) -> None:
        grant_key = str(result.get("grant_key") or "")
        if not grant_key:
            return
        payload = self._grant_payload_from_result(result)
        self._grants[grant_key] = payload
        broad_key = str(result.get("broad_grant_key") or "")
        if broad_key:
            broad_payload = dict(payload)
            broad_payload["scope"] = "broad"
            broad_payload["summary"] = str(result.get("broad_summary") or payload.get("summary") or "")
            self._grants[broad_key] = broad_payload
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

    def _grant_payload_from_result(self, result: dict[str, Any]) -> dict[str, Any]:
        summary = str(
            result.get("summary")
            or result.get("question")
            or result.get("prompt")
            or result.get("final")
            or result.get("tool")
            or "permission"
        )
        return {
            "kind": str(result.get("kind") or "tool"),
            "tool": str(result.get("tool") or ""),
            "risk": str(result.get("risk") or "low"),
            "summary": summary,
            "reason": str(result.get("reason") or ""),
            "details": result.get("details") if isinstance(result.get("details"), dict) else {},
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
        tool_name = TOOL_ALIASES.get(tool_name, tool_name)

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
        if action == "allow_always":
            self.store.allow_result(result)
            return {"action": "allow", "context": _approval_context(None, persistent=True)}
        if action == "allow_once":
            if request is not None:
                self._one_time_grants.add(request.grant_key)
            elif grant_key:
                self._one_time_grants.add(grant_key)
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
        """
        根据具体的工具及其参数，评估风险并生成权限请求单。

        :param tool_name: AI 试图调用的工具名称（例如 "file_write", "code_run"）
        :param args: AI 为该工具生成的具体参数（例如 {"path": "test.py", "script": "print('hello')"}）
        :param workspace: 当前的工作区根目录（用于解析相对路径，防止越权访问）
        :return: PermissionRequest 对象（需要拦截询问），如果认为安全则返回 None（直接放行）
        """

        # 1. 白名单放行：纯计算或内部控制类的安全工具，直接返回 None
        if tool_name in SAFE_TOOLS:
            return None

        # 2. 针对【读取文件】的特殊检查：读取通常是安全的，但绝不能静默读取密码或环境配置文件
        if tool_name == "file_read":
            raw_path = str(args.get("path", ""))
            # 尝试将路径限制在 workspace 内
            target = self._resolve_target(str(args.get("path", "")), workspace)

            # 情况A：路径解析成功，且命中敏感路径规则（如 .git, node_modules）
            if target and _is_sensitive_path(target):
                return PermissionRequest(
                    kind="tool",
                    tool=tool_name,
                    risk="high",  # 高风险！
                    reason="reading a sensitive local path needs confirmation",
                    summary=f"读取 {target}",
                    details={"path": str(target)},
                )
            # 情况B：路径可能因为某种原因没解析出来，但它的名字本身就像是秘钥（如 .env, id_rsa），这是一种纵深防御
            if raw_path and target is None and _looks_secret_name(raw_path):
                return PermissionRequest(
                    kind="tool",
                    tool=tool_name,
                    risk="high",  # 高风险！
                    reason="reading a sensitive local path needs confirmation",
                    summary=f"读取 {raw_path}",
                    details={"path": raw_path},
                )
            # 如果不是敏感文件，读取操作直接放行
            return None

        # 3. 针对【运行代码】的特殊检查：这是最高危的操作之一
        if tool_name == "code_run":
            script = str(args.get("script", args.get("code", "")))
            # 静态分析：检查代码里有没有明显的破坏性指令（如 rm -rf, os.system('shutdown')）
            blocked = self._blocked_code(script)
            code_type = str(args.get("type", "python"))
            cwd = str(args.get("cwd", ""))

            # 风险定级：如果有破坏性指令，或者不是 python 代码（如 bash/cmd 更难沙箱化），则定为高危
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
                    # 截断太长的代码，防止前端弹窗被撑爆
                    "preview": _truncate(script, 600),
                    "pattern": blocked,
                },
            )

        # 4. 针对【写入/修改文件】的特殊检查
        if tool_name in {"file_write", "file_patch"}:
            target = self._resolve_target(str(args.get("path", "")), workspace)
            path_text = str(target) if target else str(args.get("path", ""))

            # 风险定级：如果是修改敏感文件（如改写 .env）定为高危，普通文件为中等
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
                    # 提供写入内容的预览
                    "preview": _preview_args(args),
                },
            )

        # 5. 针对【浏览器网页扫描】的检查
        if tool_name == "web_scan":
            url = str(args.get("url", ""))
            return PermissionRequest(
                kind="tool",
                tool=tool_name,
                risk="low",  # 低风险，但仍需告知用户
                reason="browser navigation may send local browser state to a site",
                summary=f"打开或扫描网页 {url or '(current tab)'}",
                details={"url": url, "tab_id": str(args.get("tab_id", ""))},
            )

        # 6. 针对【网页执行 JS 代码】的检查
        if tool_name == "web_execute_js":
            script = str(args.get("script", ""))
            return PermissionRequest(
                kind="tool",
                tool=tool_name,
                risk="medium",  # 中风险（可能导致 XSS 或提取私密数据）
                reason="browser JavaScript execution needs confirmation",
                summary="在浏览器页面执行 JavaScript",
                details={
                    "tab_id": str(args.get("tab_id", "")),
                    "script": script,
                    "preview": _truncate(script, 600),
                },
            )

        # 7. 针对【屏幕截图】的检查
        if tool_name == "screenshot":
            window_title = str(args.get("window_title") or "").strip()
            window_pid = str(args.get("window_pid") or "").strip()
            window_exe = str(args.get("window_exe") or "").strip()
            details = {"monitor": str(args.get("monitor", 1))}
            if window_title:
                details["window_title"] = window_title
            if window_pid:
                details["window_pid"] = window_pid
            if window_exe:
                details["window_exe"] = window_exe

            target_parts = []
            if window_title:
                target_parts.append(f"title~{window_title}")
            if window_pid:
                target_parts.append(f"pid={window_pid}")
            if window_exe:
                target_parts.append(f"exe~{window_exe}")
            summary = "截取目标窗口 " + ", ".join(target_parts) if target_parts else "截取当前屏幕"
            return PermissionRequest(
                kind="tool",
                tool=tool_name,
                risk="medium",  # 中风险（屏幕上可能有密码、私聊等敏感信息）
                reason="screen capture may include private information",
                summary=summary,
                details=details,
            )

        # 8. 针对【派生子 Agent】的检查
        if tool_name == "spawn_subagent":
            return PermissionRequest(
                kind="tool",
                tool=tool_name,
                risk="medium",  # 中风险（相当于给 AI 找了个分身，可能消耗大量资源或产生不可控行为）
                reason="subagent execution can run additional tool loops",
                summary="派生子 agent 执行任务",
                details={"preview": _preview_args(args)},
            )

        # 9. 兜底策略：处理其他所有标记为需要询问的工具
        if tool_name in ASK_TOOLS:
            return PermissionRequest(
                kind="tool",
                tool=tool_name,
                risk="medium",
                reason="side-effecting tool needs confirmation",
                summary=f"执行工具 {tool_name}",
                details={"preview": _preview_args(args)},
            )

        # 如果工具既不在 SAFE_TOOLS 也不在 ASK_TOOLS，且没有特殊处理，默认放行
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
            broad_grant_key=request.broad_grant_key,
            broad_summary=request.broad_summary,
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


class GatewayPermissionEngine(PermissionEngine):
    """Permission engine for untrusted QQ/WeChat/Feishu gateway messages.

    Remote chat users must not be able to approve access to the host machine.
    The default tool surface is intentionally tiny; explicit env configuration
    may add read-only tools for trusted deployments, but permission prompts are
    still denied instead of being delegated to the remote chat.
    """

    def __init__(
        self,
        allowed_tools: set[str] | None = None,
        allowed_read_roots: list[Path] | None = None,
    ) -> None:
        super().__init__(
            level="locked",
            store_path=None,
            ask_on_task_mutation=False,
            ask_on_tool_mutation=True,
        )
        self.allowed_tools = allowed_tools if allowed_tools is not None else _gateway_allowed_tools()
        self.allowed_read_roots = _gateway_read_roots(allowed_read_roots)

    def assess_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        workspace: Path | None = None,
        session_context: str = "",
    ) -> PermissionDecision:
        tool_name = TOOL_ALIASES.get(tool_name, tool_name)
        if tool_name in GATEWAY_ALWAYS_DENY_TOOLS:
            return PermissionDecision(
                "deny",
                "remote gateway first principle denied host-machine access",
                tool=tool_name,
                risk="high",
            )
        if tool_name not in self.allowed_tools:
            return PermissionDecision(
                "deny",
                "remote gateway policy denied local tool access",
                tool=tool_name,
                risk="high",
                details={"allowed_tools": sorted(self.allowed_tools)},
            )

        if tool_name in {"file_read", "ocr"}:
            allowed, target, reason = _gateway_path_allowed(
                str(args.get("path") or ""),
                workspace,
                self.allowed_read_roots,
            )
            if not allowed:
                return PermissionDecision(
                    "deny",
                    reason,
                    tool=tool_name,
                    risk="high",
                    details={"path": str(target) if target else str(args.get("path") or "")},
                )
            return PermissionDecision(
                "allow",
                "remote gateway read-only attachment/workspace access",
                tool=tool_name,
                details={"path": str(target)},
            )

        if tool_name == "web_fetch":
            allowed, reason, details = _gateway_public_url_allowed(args)
            if not allowed:
                return PermissionDecision(
                    "deny",
                    reason,
                    tool=tool_name,
                    risk="high",
                    details=details,
                )
            return PermissionDecision(
                "allow",
                "remote gateway public web fetch",
                tool=tool_name,
                details=details,
            )

        if tool_name in GATEWAY_DEFAULT_TOOLS:
            return PermissionDecision("allow", "remote gateway safe tool", tool=tool_name)

        decision = super().assess_tool(tool_name, args, workspace=workspace, session_context=session_context)
        if decision.needs_user:
            return PermissionDecision(
                "deny",
                "remote gateway users cannot approve host permissions",
                tool=tool_name,
                risk=decision.risk,
                details=decision.details,
            )
        return decision

    def resolve_user_choice(self, result: dict[str, Any], choice: str) -> dict[str, Any]:
        del result, choice
        return {
            "action": "deny",
            "context": "远程网关会话不能批准本机权限请求。",
        }


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


def _gateway_allowed_tools() -> set[str]:
    allowed = set(GATEWAY_DEFAULT_TOOLS)
    raw = os.getenv("CHRYSALIS_GATEWAY_ALLOWED_TOOLS", "").strip()
    if not raw:
        return allowed
    if raw == "*":
        return set(get_registry()) - GATEWAY_ALWAYS_DENY_TOOLS
    allowed.update(
        item.strip()
        for item in re.split(r"[,;\s]+", raw)
        if item.strip()
    )
    return allowed - GATEWAY_ALWAYS_DENY_TOOLS


def _gateway_read_roots(roots: list[Path] | None) -> list[Path]:
    candidates = roots if roots is not None else [PROJECT_ROOT / "data" / "gateway"]
    result: list[Path] = []
    for root in candidates:
        try:
            resolved = Path(root).expanduser().resolve()
        except OSError:
            continue
        if resolved not in result:
            result.append(resolved)
    return result


def _gateway_path_allowed(
    path: str,
    workspace: Path | None,
    allowed_roots: list[Path],
) -> tuple[bool, Path | None, str]:
    if not path:
        return False, None, "remote gateway file path is required"
    try:
        target = safe_path(path, workspace)
    except Exception:
        return False, None, "remote gateway first principle denied host file access"
    if _is_sensitive_path(target):
        return False, target, "remote gateway first principle denied sensitive host path"
    if not target.exists() or not target.is_file():
        return False, target, "remote gateway file was not found"
    for root in allowed_roots:
        try:
            target.relative_to(root)
            return True, target, ""
        except ValueError:
            continue
    return False, target, "remote gateway first principle denied host file access"


def _gateway_public_url_allowed(args: dict[str, Any]) -> tuple[bool, str, dict[str, str]]:
    url = str(args.get("url") or "").strip()
    details = {"url": url}
    error = _public_url_error(url)
    if error:
        return False, error, details
    return True, "", details


def _public_url_error(url: str) -> str:
    if not url:
        return "remote gateway public web fetch requires an explicit URL"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "remote gateway public web fetch only allows HTTP/HTTPS URLs"
    if parsed.username or parsed.password:
        return "remote gateway public web fetch denied embedded credentials"
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return "remote gateway public web fetch requires a host"
    if host == "localhost" or host.endswith((".localhost", ".local", ".lan", ".internal", ".home.arpa")):
        return "remote gateway first principle denied local or private network access"
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return ""
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return "remote gateway first principle denied local or private network access"
    return ""


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
