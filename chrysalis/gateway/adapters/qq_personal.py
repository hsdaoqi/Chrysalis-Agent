"""Personal QQ adapter via OneBot v11 WebSocket."""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from chrysalis.gateway.adapters.base import TextPlatformAdapter, env_bool, env_list, public_access
from chrysalis.gateway.events import MessageEvent, SendResult, SessionSource
from chrysalis.gateway.service import GatewayService
from configs.config import project_path


MEDIA_DIR = project_path("data/gateway/qq_personal_media")
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

CQ_RE = re.compile(r"\[CQ:([a-zA-Z0-9_]+)(?:,([^\]]*))?\]")


@dataclass
class QQPersonalConfig:
    ws_url: str = field(default_factory=lambda: os.getenv("CHRYSALIS_ONEBOT_WS_URL", "ws://127.0.0.1:3001").strip())
    access_token: str = field(default_factory=lambda: os.getenv("CHRYSALIS_ONEBOT_ACCESS_TOKEN", "").strip())
    allowed_users: set[str] = field(default_factory=lambda: env_list("CHRYSALIS_ONEBOT_ALLOWED_USERS"))
    allowed_groups: set[str] = field(default_factory=lambda: env_list("CHRYSALIS_ONEBOT_ALLOWED_GROUPS"))
    allow_all: bool = field(default_factory=lambda: env_bool("CHRYSALIS_ONEBOT_ALLOW_ALL", False))
    require_mention: bool = field(default_factory=lambda: env_bool("CHRYSALIS_ONEBOT_REQUIRE_MENTION", True))
    reply_with_mention: bool = field(default_factory=lambda: env_bool("CHRYSALIS_ONEBOT_REPLY_WITH_MENTION", True))
    trigger_prefixes: set[str] = field(default_factory=lambda: env_list("CHRYSALIS_ONEBOT_TRIGGER_PREFIXES"))
    split_limit: int = field(default_factory=lambda: int(os.getenv("CHRYSALIS_ONEBOT_SPLIT_LIMIT", "3500")))


class QQPersonalAdapter(TextPlatformAdapter):
    label = "Personal QQ"
    platform = "qq_personal"

    def __init__(self, service: GatewayService, config: QQPersonalConfig | None = None) -> None:
        self.service = service
        self.config = config or QQPersonalConfig()
        self.split_limit = self.config.split_limit
        self.media_dir = MEDIA_DIR
        self.self_id = ""
        self._ws: Any = None
        self._ws_lock = threading.RLock()
        self._seen: deque[str] = deque(maxlen=2000)
        self._echo_seq = 0
        self._event_loop = asyncio.new_event_loop()
        self._event_thread = threading.Thread(
            target=self._run_event_loop,
            name="qq-personal-event-loop",
            daemon=True,
        )
        self._event_thread.start()

    def _run_event_loop(self) -> None:
        asyncio.set_event_loop(self._event_loop)
        self._event_loop.run_forever()

    def _submit_event(self, event: MessageEvent) -> None:
        """Schedule gateway handling without blocking the OneBot receive loop."""
        future = asyncio.run_coroutine_threadsafe(self.service.handle_event(self, event), self._event_loop)

        def _log_error(done: Any) -> None:
            try:
                done.result()
            except Exception as exc:
                print(f"[QQ personal] handle_event error: {exc}", flush=True)

        future.add_done_callback(_log_error)

    def run_forever(self) -> None:
        if not self.config.ws_url:
            raise SystemExit("Set CHRYSALIS_ONEBOT_WS_URL first.")
        websocket = _load_websocket()
        delay, max_delay = 3, 120
        while True:
            started_at = time.monotonic()
            try:
                headers = []
                if self.config.access_token:
                    headers.append(f"Authorization: Bearer {self.config.access_token}")
                print(f"[QQ personal] connecting to {self.config.ws_url}", flush=True)
                ws = websocket.create_connection(self.config.ws_url, header=headers, timeout=15)
                ws.settimeout(None)
                with self._ws_lock:
                    self._ws = ws
                print("[QQ personal] connected", flush=True)
                self._recv_loop(ws)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"[QQ personal] connection error: {exc}", flush=True)
            finally:
                with self._ws_lock:
                    try:
                        if self._ws is not None:
                            self._ws.close()
                    except Exception:
                        pass
                    self._ws = None
            if time.monotonic() - started_at >= 60:
                delay = 3
            print(f"[QQ personal] reconnect in {delay}s...", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, max_delay)

    async def send_text(self, source: SessionSource, text: str) -> SendResult:
        last_echo = None
        try:
            for idx, part in enumerate(self.split_text(text)):
                message = _cq_escape(part)
                if idx == 0 and source.chat_type == "group" and self.config.reply_with_mention:
                    message = self._group_reply_prefix(source) + message
                last_echo = self._send_msg(source, message)
            return SendResult(True, message_id=last_echo)
        except Exception as exc:
            print(f"[QQ personal] send_text error: {exc}", flush=True)
            return SendResult(False, error=str(exc))

    async def send_image(self, source: SessionSource, file_path: str) -> SendResult:
        return await self._send_cq_media(source, file_path, cq_type="image")

    async def send_video(self, source: SessionSource, file_path: str) -> SendResult:
        return await self._send_cq_media(source, file_path, cq_type="video")

    async def send_file(self, source: SessionSource, file_path: str) -> SendResult:
        path = str(file_path).strip()
        if not path:
            return SendResult(False, error="empty file path")
        if _is_url(path):
            return await self.send_text(source, f"[FILE:{path}]")
        local = Path(path)
        if not local.exists():
            return SendResult(False, error=f"file not found: {file_path}")
        try:
            if source.chat_type == "group":
                echo = self._send_action(
                    "upload_group_file",
                    {"group_id": int(source.chat_id), "file": str(local.resolve()), "name": local.name},
                )
            else:
                echo = self._send_action(
                    "upload_private_file",
                    {"user_id": int(source.chat_id), "file": str(local.resolve()), "name": local.name},
                )
            return SendResult(True, message_id=echo)
        except Exception as exc:
            print(f"[QQ personal] send_file error: {exc}", flush=True)
            return await self.send_text(source, f"[FILE:{path}]")

    async def _send_cq_media(self, source: SessionSource, file_path: str, *, cq_type: str) -> SendResult:
        path = str(file_path).strip()
        if not path:
            return SendResult(False, error="empty media path")
        try:
            value = path if _is_url(path) else _file_uri(path)
            echo = self._send_msg(source, f"[CQ:{cq_type},file={_cq_param_escape(value)}]")
            return SendResult(True, message_id=echo)
        except Exception as exc:
            print(f"[QQ personal] send_{cq_type} error: {exc}", flush=True)
            return SendResult(False, error=str(exc))

    def _recv_loop(self, ws: Any) -> None:
        while True:
            raw = ws.recv()
            if not raw:
                continue
            try:
                packet = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(packet, dict):
                continue
            if "self_id" in packet:
                self.self_id = str(packet.get("self_id") or self.self_id)
            if packet.get("echo") is not None and packet.get("status") is not None:
                continue
            self._handle_packet(packet)

    def _handle_packet(self, packet: dict[str, Any]) -> None:
        if packet.get("post_type") != "message":
            return
        message_type = str(packet.get("message_type") or "").lower()
        if message_type not in {"private", "group"}:
            return

        message_id = str(packet.get("message_id") or "")
        if message_id and message_id in self._seen:
            return
        if message_id:
            self._seen.append(message_id)

        user_id = str(packet.get("user_id") or "")
        if self.self_id and user_id == self.self_id:
            return
        if not public_access(self.config.allowed_users, self.config.allow_all) and user_id not in self.config.allowed_users:
            print(f"[QQ personal] unauthorized user: {user_id}", flush=True)
            return

        is_group = message_type == "group"
        chat_id = str(packet.get("group_id") if is_group else user_id)
        if is_group and self.config.allowed_groups and chat_id not in self.config.allowed_groups:
            print(f"[QQ personal] ignored group: {chat_id}", flush=True)
            return

        text, media_paths, mentioned = self._parse_message(packet.get("message"))
        text, triggered = self._apply_group_trigger(text, mentioned=mentioned, is_group=is_group)
        if is_group and not triggered:
            return
        if not text and not media_paths:
            return

        sender = packet.get("sender") if isinstance(packet.get("sender"), dict) else {}
        user_name = str(sender.get("card") or sender.get("nickname") or "")
        source = SessionSource(
            platform=self.platform,
            chat_id=chat_id,
            chat_type="group" if is_group else "dm",
            user_id=user_id,
            user_name=user_name,
            message_id=message_id or None,
            metadata={"message_type": message_type, "self_id": self.self_id},
        )
        event = MessageEvent(text=text, source=source, raw_message=packet, media_paths=media_paths)
        print(f"[QQ personal] message from {user_id} ({source.chat_type}): {text[:80]}", flush=True)
        self._submit_event(event)

    def _parse_message(self, message: Any) -> tuple[str, list[str], bool]:
        if isinstance(message, list):
            return self._parse_segments(message)
        if isinstance(message, str):
            return self._parse_cq_string(message)
        return str(message or "").strip(), [], False

    def _parse_segments(self, segments: list[Any]) -> tuple[str, list[str], bool]:
        text_parts: list[str] = []
        media_paths: list[str] = []
        mentioned = False
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            seg_type = str(segment.get("type") or "")
            data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
            if seg_type == "text":
                text_parts.append(str(data.get("text") or ""))
            elif seg_type == "at":
                if self._is_self_mention(str(data.get("qq") or "")):
                    mentioned = True
            elif seg_type in {"image", "file", "video", "record"}:
                media = self._resolve_media(data, seg_type)
                if media:
                    media_paths.append(media)
        return " ".join(part.strip() for part in text_parts if part.strip()).strip(), media_paths, mentioned

    def _parse_cq_string(self, raw: str) -> tuple[str, list[str], bool]:
        text_parts: list[str] = []
        media_paths: list[str] = []
        mentioned = False
        pos = 0
        for match in CQ_RE.finditer(raw):
            if match.start() > pos:
                text_parts.append(_cq_unescape(raw[pos:match.start()]))
            cq_type = match.group(1).lower()
            params = _parse_cq_params(match.group(2) or "")
            if cq_type == "at" and self._is_self_mention(params.get("qq", "")):
                mentioned = True
            elif cq_type in {"image", "file", "video", "record"}:
                media = self._resolve_media(params, cq_type)
                if media:
                    media_paths.append(media)
            pos = match.end()
        if pos < len(raw):
            text_parts.append(_cq_unescape(raw[pos:]))
        return " ".join(part.strip() for part in text_parts if part.strip()).strip(), media_paths, mentioned

    def _resolve_media(self, data: dict[str, Any], media_type: str) -> str:
        url = str(data.get("url") or "").strip()
        if url:
            downloaded = self._download_media(url, media_type)
            if downloaded:
                return downloaded
        value = str(data.get("file") or data.get("path") or "").strip()
        if value.startswith("file://"):
            return unquote(urlparse(value).path.lstrip("/")) if os.name == "nt" else unquote(urlparse(value).path)
        if value and (Path(value).exists() or re.match(r"^[a-zA-Z]:[\\/]", value)):
            return value
        return ""

    def _download_media(self, url: str, media_type: str) -> str:
        try:
            import requests
        except Exception:
            return ""
        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            ext = _suffix_from_url(url) or _suffix_from_content_type(response.headers.get("content-type", ""))
            if not ext:
                ext = ".bin"
            target = self.media_dir / f"onebot_{media_type}_{int(time.time() * 1000)}{ext}"
            target.write_bytes(response.content)
            return str(target.resolve())
        except Exception as exc:
            print(f"[QQ personal] media download failed: {exc}", flush=True)
            return ""

    def _apply_group_trigger(self, text: str, *, mentioned: bool, is_group: bool) -> tuple[str, bool]:
        if not is_group:
            return text, True
        stripped = text.strip()
        for prefix in sorted(self.config.trigger_prefixes, key=len, reverse=True):
            if prefix and stripped.startswith(prefix):
                return stripped[len(prefix):].strip(), True
        if not self.config.require_mention:
            return stripped, True
        return stripped, mentioned

    def _is_self_mention(self, qq: str) -> bool:
        qq = str(qq).strip()
        return bool(qq and self.self_id and qq == self.self_id)

    def _group_reply_prefix(self, source: SessionSource) -> str:
        parts: list[str] = []
        if source.message_id:
            parts.append(f"[CQ:reply,id={_cq_param_escape(source.message_id)}]")
        if source.user_id:
            parts.append(f"[CQ:at,qq={_cq_param_escape(source.user_id)}] ")
        return "".join(parts)

    def _send_msg(self, source: SessionSource, message: str) -> str:
        params: dict[str, Any]
        if source.chat_type == "group":
            params = {"group_id": int(source.chat_id), "message": message, "auto_escape": False}
            return self._send_action("send_group_msg", params)
        params = {"user_id": int(source.chat_id), "message": message, "auto_escape": False}
        return self._send_action("send_private_msg", params)

    def _send_action(self, action: str, params: dict[str, Any]) -> str:
        payload = {
            "action": action,
            "params": params,
            "echo": self._next_echo(),
        }
        body = json.dumps(payload, ensure_ascii=False)
        with self._ws_lock:
            if self._ws is None:
                raise RuntimeError("OneBot WebSocket is not connected")
            self._ws.send(body)
        return str(payload["echo"])

    def _next_echo(self) -> str:
        self._echo_seq += 1
        return f"chrysalis-{int(time.time() * 1000)}-{self._echo_seq}"


def _load_websocket():
    try:
        import websocket
    except Exception as exc:
        raise SystemExit("Install OneBot support with: pip install websocket-client") from exc
    return websocket


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _file_uri(path: str) -> str:
    return Path(path).resolve().as_uri()


def _suffix_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix
    return suffix[:12] if suffix else ""


def _suffix_from_content_type(content_type: str) -> str:
    content_type = content_type.split(";", 1)[0].strip().lower()
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
    }
    return mapping.get(content_type, "")


def _cq_escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("[", "&#91;").replace("]", "&#93;")


def _cq_param_escape(text: str) -> str:
    return _cq_escape(text).replace(",", "&#44;")


def _cq_unescape(text: str) -> str:
    return (
        (text or "")
        .replace("&#91;", "[")
        .replace("&#93;", "]")
        .replace("&#44;", ",")
        .replace("&amp;", "&")
    )


def _parse_cq_params(raw: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for part in raw.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        params[key.strip()] = _cq_unescape(value.strip())
    return params
