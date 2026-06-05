"""Gateway service that routes platform messages into Chrysalis kernels."""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from chrysalis.gateway.activity import GatewayActivityStore
from chrysalis.gateway.events import (
    GATEWAY_FIRST_PRINCIPLE,
    MessageEvent,
    SendResult,
    SessionSource,
    build_session_context,
    build_session_key,
)
from chrysalis.kernel import Kernel
from chrysalis.permission import GatewayPermissionEngine
from chrysalis.tools import generate_tool_prompt, generate_tools_schema, get_registry
from configs.config import AgentConfig


HELP_TEXT = """Chrysalis gateway commands:
/help - Show this help
/status - Show current session and model
/stop - Stop the current task
/new or /reset - Start a fresh session for this chat
/session - Show this gateway session
/session new - Start a fresh session for this chat
"""

FILE_TAG_RE = re.compile(r"\[FILE:([^\]]+)\]")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".tif"}
GATEWAY_ATTACHMENT_DENY_NOTE = "部分附件因远程网关安全策略被忽略。"


class GatewayAdapter(Protocol):
    label: str
    split_limit: int

    async def send_text(self, source: SessionSource, text: str) -> SendResult:
        ...

    async def send_image(self, source: SessionSource, file_path: str) -> SendResult:
        ...

    async def send_file(self, source: SessionSource, file_path: str) -> SendResult:
        ...

    async def send_video(self, source: SessionSource, file_path: str) -> SendResult:
        ...


@dataclass
class SessionBinding:
    session_key: str
    session_id: str
    kernel: Kernel


class GatewaySessionMap:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data: dict[str, str] = {}
        self.last_error = ""
        self._load()

    def get(self, session_key: str) -> str | None:
        with self._lock:
            return self._data.get(session_key)

    def set(self, session_key: str, session_id: str) -> None:
        with self._lock:
            self._data[session_key] = session_id
            self._save()

    def delete(self, session_key: str) -> None:
        with self._lock:
            self._data.pop(session_key, None)
            self._save()

    def _load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            self._data = {str(k): str(v) for k, v in data.items()}

    def _save(self) -> None:
        payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        last_error: OSError | None = None
        for attempt in range(12):
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
            try:
                tmp.write_text(payload, encoding="utf-8")
                os.replace(tmp, self.path)
                self.last_error = ""
                return
            except OSError as exc:
                last_error = exc
                if attempt < 11:
                    import time

                    time.sleep(0.05 * (attempt + 1))
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
        if last_error is not None:
            self.last_error = f"{type(last_error).__name__}: {last_error}"


class GatewayService:
    def __init__(
        self,
        config: AgentConfig | None = None,
        *,
        group_sessions_per_user: bool = True,
        thread_sessions_per_user: bool = False,
    ) -> None:
        self.config = config or AgentConfig()
        self.group_sessions_per_user = group_sessions_per_user
        self.thread_sessions_per_user = thread_sessions_per_user
        self.session_map = GatewaySessionMap(self.config.data_dir / "gateway_sessions.json")
        self.activity = GatewayActivityStore(self.config.data_dir / "gateway_activity.json")
        self._bindings: dict[str, SessionBinding] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = threading.RLock()
        self._gateway_media_roots = _gateway_media_roots(self.config)
        self._gateway_outgoing_roots = _gateway_outgoing_roots(self.config)

    async def handle_event(self, adapter: GatewayAdapter, event: MessageEvent) -> None:
        text = (event.text or "").strip()
        if not text and not event.media_paths:
            return
        binding = self._binding_for(event.source)
        if event.is_command():
            await self._handle_command(adapter, event, binding)
            return
        await self._run_task(adapter, event, binding)

    def _binding_for(self, source: SessionSource) -> SessionBinding:
        session_key = build_session_key(
            source,
            group_sessions_per_user=self.group_sessions_per_user,
            thread_sessions_per_user=self.thread_sessions_per_user,
        )
        with self._guard:
            existing = self._bindings.get(session_key)
            if existing is not None:
                return existing

            session_id = self.session_map.get(session_key)
            if session_id:
                try:
                    kernel = self._create_gateway_kernel(session_id=session_id)
                except Exception:
                    kernel = self._create_gateway_kernel()
                    session_id = kernel.session_store.current_id or kernel.new_session()
                    self.session_map.set(session_key, session_id)
            else:
                kernel = self._create_gateway_kernel()
                session_id = kernel.session_store.current_id or kernel.new_session()
                self.session_map.set(session_key, session_id)

            binding = SessionBinding(session_key=session_key, session_id=session_id, kernel=kernel)
            self._bindings[session_key] = binding
            return binding

    def _create_gateway_kernel(self, session_id: str | None = None) -> Kernel:
        kernel = Kernel(config=self.config, session_id=session_id)
        permission_engine = GatewayPermissionEngine(allowed_read_roots=self._gateway_media_roots)
        kernel.loop.permission_engine = permission_engine
        kernel.permission_engine = permission_engine
        kernel.loop.system_prompt_preamble = GATEWAY_FIRST_PRINCIPLE
        allowed_tools = set(permission_engine.allowed_tools)
        exclude = set(get_registry()) - allowed_tools
        kernel.loop.tools_schema = generate_tools_schema(exclude=exclude)
        kernel.loop.tool_prompt = generate_tool_prompt(exclude=exclude)
        return kernel

    def _lock_for(self, session_key: str) -> asyncio.Lock:
        with self._guard:
            lock = self._locks.get(session_key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[session_key] = lock
            return lock

    async def _run_task(self, adapter: GatewayAdapter, event: MessageEvent, binding: SessionBinding) -> None:
        lock = self._lock_for(binding.session_key)
        if lock.locked():
            await adapter.send_text(event.source, "上一条任务还在处理，这条消息会排队。发送 /stop 可以中断当前任务。")

        async with lock:
            await adapter.send_text(event.source, "收到，正在处理...")
            task, images = self._build_payload(event)
            task_id = uuid.uuid4().hex
            result: dict[str, Any] = {
                "ok": False,
                "error": "Task did not start.",
                "final": "Task did not start.",
            }
            self.activity.start_task(
                task_id=task_id,
                session_id=binding.session_id,
                session_key=binding.session_key,
                platform=event.source.platform,
                source=self._activity_source(event.source),
                task=task,
                model=binding.kernel.active_model_name,
            )
            self._bind_activity_callbacks(binding, task_id)
            try:
                session_context = build_session_context(event.source, binding.session_key, binding.session_id)
                result = await asyncio.to_thread(binding.kernel.run, task, session_context, images=images)
            except Exception as exc:
                internal_error = f"{type(exc).__name__}: {exc}"
                result = {
                    "ok": False,
                    "error": internal_error,
                    "final": _remote_error_text(internal_error),
                }
            finally:
                binding.session_id = binding.kernel.session_store.current_id or binding.session_id
                self.session_map.set(binding.session_key, binding.session_id)
                self.activity.update_session(task_id, binding.session_id)
                self.activity.finish_task(task_id, result)
            await self._deliver_result(adapter, event.source, result)

    def _bind_activity_callbacks(self, binding: SessionBinding, task_id: str) -> None:
        turn = {"value": 0}

        def on_progress(message: str) -> None:
            self.activity.status(task_id, message)

        def on_tool_call(tool: str, args: dict, observation: dict | None) -> None:
            if observation is None:
                turn["value"] += 1
                self.activity.tool_started(task_id, tool, args, turn["value"])
                return
            self.activity.tool_completed(task_id, tool, observation, turn["value"])

        binding.kernel.progress = on_progress
        binding.kernel.loop.progress = on_progress
        binding.kernel.loop.on_stream_chunk = lambda chunk: self.activity.append_stream(task_id, str(chunk or ""))
        binding.kernel.loop.on_tool_call = on_tool_call
        binding.kernel.loop.on_thinking = (
            lambda text: self.activity.append_stream(task_id, str(text or ""), kind="thinking")
        )
        binding.kernel.loop.on_working_change = lambda snapshot: self.activity.working_changed(task_id, snapshot)
        binding.kernel.loop.on_trace_event = lambda payload: self.activity.trace_event(task_id, payload)
        binding.kernel.llm.on_trace_event = lambda payload: self.activity.trace_event(task_id, payload)

    def _activity_source(self, source: SessionSource) -> dict[str, Any]:
        return {
            "platform": source.platform,
            "chat_id": source.chat_id,
            "chat_type": source.chat_type,
            "user_id": source.user_id,
            "user_name": source.user_name,
            "thread_id": source.thread_id,
            "chat_name": source.chat_name,
            "message_id": source.message_id,
            "description": source.description,
        }

    def _build_payload(self, event: MessageEvent) -> tuple[str, list[dict]]:
        text = (event.text or "").strip()
        image_blocks: list[dict] = []
        non_image_paths: list[str] = []
        ignored_media = 0

        for raw_path in event.media_paths:
            path = str(raw_path).strip()
            if not path:
                continue
            if not self._is_allowed_gateway_media(path):
                ignored_media += 1
                continue
            suffix = Path(path).suffix.lower()
            if suffix in IMAGE_EXTS:
                try:
                    from chrysalis.llm.image_utils import prepare_image_from_path

                    media_type, data = prepare_image_from_path(path)
                    image_blocks.append({"media_type": media_type, "data": data})
                    continue
                except Exception:
                    pass
            non_image_paths.append(path)

        lines: list[str] = []
        if text:
            lines.append(text)
        if non_image_paths:
            if lines:
                lines.append("")
            lines.append("Attachments:")
            lines.extend(f"- {path}" for path in non_image_paths)

        if not lines:
            if image_blocks:
                lines.append("Please analyze the attached image(s) and reply to the user.")
            else:
                lines.append("The user attached files. Inspect the attachments and reply.")
        if ignored_media:
            if lines:
                lines.append("")
            lines.append(GATEWAY_ATTACHMENT_DENY_NOTE)

        return "\n".join(lines).strip(), image_blocks

    def _is_allowed_gateway_media(self, path: str) -> bool:
        try:
            target = Path(path).expanduser().resolve()
        except OSError:
            return False
        if not target.exists() or not target.is_file():
            return False
        for root in self._gateway_media_roots:
            try:
                target.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    async def _deliver_result(self, adapter: GatewayAdapter, source: SessionSource, result: dict) -> None:
        text, files = self._extract_result_assets(result)
        sent_anything = False
        if text:
            await adapter.send_text(source, text)
            sent_anything = True
        for path in files:
            await self._send_attachment(adapter, source, path)
            sent_anything = True
        if not sent_anything:
            await adapter.send_text(source, "...")

    def _extract_result_assets(self, result: dict) -> tuple[str, list[str]]:
        raw = self._format_result(result)
        text = self._clean_text(raw)
        files = list(FILE_TAG_RE.findall(raw))
        extra_files = result.get("files") or []
        if isinstance(extra_files, (list, tuple, set)):
            files.extend(str(item) for item in extra_files)
        seen: set[str] = set()
        cleaned_files: list[str] = []
        for item in files:
            path = str(item).strip()
            if not path or path in seen:
                continue
            if not self._is_allowed_outgoing_attachment(path):
                continue
            seen.add(path)
            cleaned_files.append(path)
        return text, cleaned_files

    def _clean_text(self, text: str) -> str:
        cleaned = text or ""
        for tag in ("thinking", "summary", "tool_use", "file_content"):
            cleaned = re.sub(fr"<{tag}>.*?</{tag}>", "", cleaned, flags=re.DOTALL)
        cleaned = FILE_TAG_RE.sub("", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = _redact_host_details(cleaned)
        return cleaned.strip()

    async def _send_attachment(self, adapter: GatewayAdapter, source: SessionSource, path: str) -> None:
        if not self._is_allowed_outgoing_attachment(path):
            await adapter.send_text(source, f"已阻止发送本机文件：{Path(path).name or path}")
            return
        suffix = Path(path).suffix.lower()
        if suffix in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
            await adapter.send_video(source, path)
        elif suffix in IMAGE_EXTS:
            await adapter.send_image(source, path)
        else:
            await adapter.send_file(source, path)

    async def _handle_command(self, adapter: GatewayAdapter, event: MessageEvent, binding: SessionBinding) -> None:
        cmd = event.command_name() or ""
        args = event.command_args()
        lock = self._lock_for(binding.session_key)

        if cmd == "help":
            await adapter.send_text(event.source, HELP_TEXT)
            return
        if cmd == "status":
            busy = "running" if lock.locked() else "idle"
            await adapter.send_text(
                event.source,
                "\n".join([
                    f"Status: {busy}",
                    f"Platform: {event.source.platform}",
                    f"Session: {binding.session_id}",
                    f"Model: {binding.kernel.active_model_name}",
                ]),
            )
            return
        if cmd == "stop":
            binding.kernel.cancel()
            self.activity.mark_session_stopping(binding.session_id)
            await adapter.send_text(event.source, "已请求停止当前任务。")
            return
        if cmd in {"new", "reset"}:
            if lock.locked():
                binding.kernel.cancel()
                await adapter.send_text(event.source, "已请求停止当前任务，结束后会开启新会话。")
            async with lock:
                session_id = binding.kernel.new_session()
                binding.session_id = session_id
                self.session_map.set(binding.session_key, session_id)
            await adapter.send_text(event.source, f"已开启新会话：{session_id}")
            return
        if cmd in {"session", "sessions", "s"}:
            await self._handle_session_command(adapter, event, binding, args)
            return
        await adapter.send_text(event.source, HELP_TEXT)

    async def _handle_session_command(
        self,
        adapter: GatewayAdapter,
        event: MessageEvent,
        binding: SessionBinding,
        args: str,
    ) -> None:
        parts = args.split()
        sub = parts[0].lower() if parts else "list"
        if sub == "new":
            sid = binding.kernel.new_session()
            binding.session_id = sid
            self.session_map.set(binding.session_key, sid)
            await adapter.send_text(event.source, f"已开启新会话：{sid}")
            return

        if sub in {"load", "delete", "remove", "rm"}:
            await adapter.send_text(event.source, "远程网关会话不能加载或删除本机历史会话。发送 /session new 可为当前聊天新建会话。")
            return

        lines = [
            "Gateway session:",
            f"- Session: {binding.session_id}",
            f"- Key: {binding.session_key}",
            "Use /session new to start a fresh session for this chat.",
        ]
        await adapter.send_text(event.source, "\n".join(lines))

    def _is_allowed_outgoing_attachment(self, path: str) -> bool:
        if _is_url(path):
            return True
        try:
            target = Path(path).expanduser().resolve()
        except OSError:
            return False
        if not target.exists() or not target.is_file():
            return False
        for root in self._gateway_outgoing_roots:
            try:
                target.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _format_result(self, result: dict) -> str:
        text = str(result.get("final") or result.get("error") or result)
        if result.get("need_user"):
            options = result.get("options") or []
            if options:
                labels = []
                for option in options:
                    if isinstance(option, dict):
                        labels.append(str(option.get("label") or option.get("id") or option))
                    else:
                        labels.append(str(option))
                text += "\n\n可回复：" + " / ".join(labels)
        usage = result.get("usage")
        if isinstance(usage, dict) and usage.get("cost"):
            text += f"\n\nCost: {usage['cost']:.6f}"
        return text.strip() or "..."


def _parse_index(parts: list[str]) -> int | None:
    if not parts:
        return None
    try:
        value = int(parts[0])
    except ValueError:
        return None
    return value - 1 if value > 0 else None


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _remote_error_text(error: str) -> str:
    del error
    return "任务执行异常：内部状态同步失败或运行时错误，请稍后重试。"


def _redact_host_details(text: str) -> str:
    redacted = str(text or "")
    redacted = re.sub(r"[A-Za-z]:\\(?:[^\\\s'\"<>|]+\\)*[^\\\s'\"<>|]*", "[host path redacted]", redacted)
    redacted = re.sub(r"/(?:Users|home|mnt|var|tmp|etc|root|opt)/[^\s'\"<>|]+", "[host path redacted]", redacted)
    redacted = re.sub(r"gateway_activity\.json\.[A-Za-z0-9_.-]+\.tmp", "gateway_activity.json.tmp", redacted)
    return redacted


def _gateway_media_roots(config: AgentConfig) -> list[Path]:
    roots: list[Path] = []
    for root in (config.data_dir / "gateway", Path("data/gateway")):
        try:
            resolved = root.expanduser().resolve()
        except OSError:
            continue
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _gateway_outgoing_roots(config: AgentConfig) -> list[Path]:
    roots: list[Path] = []
    for root in (config.workspace_dir, config.data_dir / "gateway"):
        try:
            resolved = root.expanduser().resolve()
        except OSError:
            continue
        if resolved not in roots:
            roots.append(resolved)
    return roots
