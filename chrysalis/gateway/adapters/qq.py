"""QQ bot adapter."""

from __future__ import annotations

import asyncio
import mimetypes
import os
from pathlib import Path
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from chrysalis.gateway.adapters.base import TextPlatformAdapter, env_bool, env_list, public_access
from chrysalis.gateway.adapters.qq_upload import (
    ChunkedUploader,
    UploadDailyLimitExceededError,
    UploadFileTooLargeError,
)
from chrysalis.gateway.events import MessageEvent, SendResult, SessionSource
from chrysalis.gateway.service import GatewayService
from configs.config import project_path


MEDIA_DIR = project_path("data/gateway/qq_media")
MEDIA_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class QQConfig:
    app_id: str = field(default_factory=lambda: os.getenv("CHRYSALIS_QQ_APP_ID", "").strip())
    app_secret: str = field(default_factory=lambda: os.getenv("CHRYSALIS_QQ_APP_SECRET", "").strip())
    allowed_users: set[str] = field(default_factory=lambda: env_list("CHRYSALIS_QQ_ALLOWED_USERS"))
    allow_all: bool = field(default_factory=lambda: env_bool("CHRYSALIS_QQ_ALLOW_ALL", False))
    split_limit: int = field(default_factory=lambda: int(os.getenv("CHRYSALIS_QQ_SPLIT_LIMIT", "1500")))


class QQAdapter(TextPlatformAdapter):
    label = "QQ"
    platform = "qq"

    def __init__(self, service: GatewayService, config: QQConfig | None = None) -> None:
        self.service = service
        self.config = config or QQConfig()
        self.split_limit = self.config.split_limit
        self.client: Any = None
        self.media_dir = MEDIA_DIR
        self._seen: deque[str] = deque(maxlen=1000)
        self._seq_lock = threading.Lock()
        self._msg_seq = 1

    async def run_forever(self) -> None:
        if not self.config.app_id or not self.config.app_secret:
            raise SystemExit("Set CHRYSALIS_QQ_APP_ID and CHRYSALIS_QQ_APP_SECRET first.")
        botpy, _c2c, _group = _load_botpy()
        self.client = self._make_client_class(botpy)()
        delay, max_delay = 5, 300
        while True:
            started_at = time.monotonic()
            try:
                print(f"[QQ] bot starting... {time.strftime('%m-%d %H:%M')}", flush=True)
                await self.client.start(appid=self.config.app_id, secret=self.config.app_secret)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[QQ] bot error: {exc}", flush=True)
            if time.monotonic() - started_at >= 60:
                delay = 5
            print(f"[QQ] reconnect in {delay}s...", flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)

    async def send_text(self, source: SessionSource, text: str) -> SendResult:
        if not self.client:
            return SendResult(False, error="QQ client is not connected")
        try:
            is_group = source.chat_type == "group"
            api = self.client.api.post_group_message if is_group else self.client.api.post_c2c_message
            key = "group_openid" if is_group else "openid"
            last_id = None
            for part in self.split_text(text):
                response = await api(
                    **{
                        key: source.chat_id,
                        "msg_type": 0,
                        "content": part,
                        "msg_id": source.message_id,
                        "msg_seq": self._next_msg_seq(),
                    }
                )
                last_id = str(getattr(response, "id", "") or getattr(response, "message_id", "") or "")
            return SendResult(True, message_id=last_id or None)
        except Exception as exc:
            print(f"[QQ] send_text error: {exc}", flush=True)
            return SendResult(False, error=str(exc))

    async def send_image(self, source: SessionSource, file_path: str) -> SendResult:
        return await self._send_media(source, file_path, file_type=1)

    async def send_file(self, source: SessionSource, file_path: str) -> SendResult:
        return await self._send_media(source, file_path, file_type=4)

    async def send_video(self, source: SessionSource, file_path: str) -> SendResult:
        return await self._send_media(source, file_path, file_type=2)

    async def on_message(self, data: Any, *, is_group: bool) -> None:
        msg_id = str(getattr(data, "id", "") or "")
        if msg_id and msg_id in self._seen:
            return
        if msg_id:
            self._seen.append(msg_id)
        author = getattr(data, "author", None)
        user_id = str(
            getattr(author, "member_openid" if is_group else "user_openid", "")
            or getattr(author, "id", "")
            or "unknown"
        )
        chat_id = str(getattr(data, "group_openid", "") or user_id) if is_group else user_id
        if not public_access(self.config.allowed_users, self.config.allow_all) and user_id not in self.config.allowed_users:
            print(f"[QQ] unauthorized user: {user_id}", flush=True)
            return

        media_paths = self._download_attachments(getattr(data, "attachments", []) or [])
        content = (getattr(data, "content", "") or "").strip()
        if not content and not media_paths:
            return

        source = SessionSource(
            platform=self.platform,
            chat_id=chat_id,
            chat_type="group" if is_group else "dm",
            user_id=user_id,
            user_name=str(getattr(author, "username", "") or getattr(author, "nick", "") or ""),
            message_id=msg_id or None,
        )
        event = MessageEvent(text=content, source=source, raw_message=data, media_paths=media_paths)
        print(f"[QQ] message from {user_id} ({source.chat_type}): {content[:80]}", flush=True)
        await self.service.handle_event(self, event)

    async def _send_media(self, source: SessionSource, file_path: str, *, file_type: int) -> SendResult:
        if not self.client:
            return SendResult(False, error="QQ client is not connected")

        path = str(file_path).strip()
        if not path:
            return SendResult(False, error="empty media path")

        if _is_url(path):
            return await self._send_remote_media(source, path, file_type=file_type)

        result = await self._send_local_media(source, path, file_type=file_type)
        if result.success:
            return result
        return result

    async def _send_remote_media(self, source: SessionSource, url: str, *, file_type: int) -> SendResult:
        try:
            is_group = source.chat_type == "group"
            api = self.client.api.post_group_file if is_group else self.client.api.post_c2c_file
            key = "group_openid" if is_group else "openid"
            response = await api(
                **{
                    key: source.chat_id,
                    "file_type": file_type,
                    "url": url,
                    "srv_send_msg": True,
                }
            )
            return SendResult(True, raw_response=response)
        except Exception as exc:
            print(f"[QQ] remote media send error: {exc}", flush=True)
            return SendResult(False, error=str(exc))

    async def _send_local_media(self, source: SessionSource, file_path: str, *, file_type: int) -> SendResult:
        path = Path(file_path)
        if not path.exists():
            return SendResult(False, error=f"file not found: {file_path}")

        try:
            await self.client.http.check_session()
            uploader = ChunkedUploader(
                api_request=self._api_request,
                http_put=self._http_put,
                log_tag="QQ",
            )
            chat_type = "group" if source.chat_type == "group" else "c2c"
            upload = await uploader.upload(
                chat_type=chat_type,
                target_id=source.chat_id,
                file_path=str(path.resolve()),
                file_type=file_type,
                file_name=path.name,
            )
            media = self._extract_media(upload)
            if not media:
                return SendResult(False, error=f"Upload returned no file_info: {upload}")

            is_group = source.chat_type == "group"
            api = self.client.api.post_group_message if is_group else self.client.api.post_c2c_message
            key = "group_openid" if is_group else "openid"
            response = await api(
                **{
                    key: source.chat_id,
                    "msg_type": 7,
                    "media": media,
                    "msg_id": source.message_id,
                    "msg_seq": self._next_msg_seq(),
                }
            )
            return SendResult(True, raw_response=response)
        except UploadDailyLimitExceededError as exc:
            print(f"[QQ] daily upload limit exceeded: {exc}", flush=True)
            return SendResult(False, error=f"{exc.file_name!r} ({exc.file_size_human}) exceeds QQ daily upload quota.")
        except UploadFileTooLargeError as exc:
            print(f"[QQ] file too large: {exc}", flush=True)
            return SendResult(False, error=f"{exc.file_name!r} ({exc.file_size_human}) exceeds QQ upload limit ({exc.limit_human}).")
        except Exception as exc:
            print(f"[QQ] local media send error: {exc}", flush=True)
            return SendResult(False, error=str(exc))

    async def _api_request(self, method: str, path: str, *, body: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        from botpy.http import Route

        del timeout
        if not self.client:
            raise RuntimeError("QQ client is not connected")
        await self.client.http.check_session()
        route = Route(method, path)
        kwargs: dict[str, Any] = {}
        if body is not None:
            kwargs["json"] = body
        response = await self.client.http.request(route, **kwargs)
        return response

    async def _http_put(self, url: str, *, data: bytes, headers: dict[str, str]) -> Any:
        import aiohttp

        if not self.client:
            raise RuntimeError("QQ client is not connected")
        await self.client.http.check_session()
        session = getattr(self.client.http, "_session", None)
        if session is None:
            raise RuntimeError("QQ HTTP session is not ready")
        timeout = aiohttp.ClientTimeout(total=300.0)
        async with session.put(url, data=data, headers=headers, timeout=timeout) as response:
            body = await response.text()
            return _PutResponse(status_code=response.status, text=body)

    @staticmethod
    def _extract_file_info(upload: dict[str, Any]) -> str:
        media = QQAdapter._extract_media(upload)
        return str(media.get("file_info", "") or "").strip() if media else ""

    @staticmethod
    def _extract_media(upload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(upload, dict):
            return {}
        src = upload.get("data") if isinstance(upload.get("data"), dict) else upload
        media: dict[str, Any] = {}
        for key in ("file_uuid", "file_info", "ttl"):
            value = src.get(key)
            if value not in (None, ""):
                media[key] = value
        if media.get("file_info"):
            return media
        data = upload.get("data")
        if isinstance(data, dict) and isinstance(data.get("media"), dict):
            nested = data["media"]
            file_info = str(nested.get("file_info", "") or "").strip()
            if file_info:
                return {key: value for key, value in nested.items() if value not in (None, "")}
        return {}

    def _download_attachments(self, attachments: list[Any]) -> list[str]:
        paths: list[str] = []
        if not attachments:
            return paths
        try:
            import requests
        except Exception:
            return paths

        for item in attachments:
            url = str(getattr(item, "url", "") or "").strip()
            if not url:
                continue
            filename = Path(str(getattr(item, "filename", "") or "").strip()).name
            content_type = str(getattr(item, "content_type", "") or "").strip()
            ext = Path(filename).suffix or _ext_from_content_type(content_type)
            if not ext:
                ext = ".bin"
            target_name = filename or f"qq_{int(time.time())}_{len(paths)}{ext}"
            target = self.media_dir / target_name
            try:
                response = requests.get(url, timeout=60)
                response.raise_for_status()
                target.write_bytes(response.content)
                paths.append(str(target.resolve()))
            except Exception as exc:
                print(f"[QQ] attachment download failed: {exc}", flush=True)
        return paths

    def _make_client_class(self, botpy):
        adapter = self

        class ChrysalisQQBot(botpy.Client):
            def __init__(self):
                super().__init__(intents=_build_intents(botpy), timeout=30, ext_handlers=False)

            async def on_ready(self):
                robot = getattr(self, "robot", None)
                name = getattr(robot, "name", "QQBot")
                print(f"[QQ] bot ready: {name}", flush=True)

            async def on_c2c_message_create(self, message):
                await adapter.on_message(message, is_group=False)

            async def on_group_at_message_create(self, message):
                await adapter.on_message(message, is_group=True)

            async def on_direct_message_create(self, message):
                await adapter.on_message(message, is_group=False)

        return ChrysalisQQBot

    def _next_msg_seq(self) -> int:
        with self._seq_lock:
            self._msg_seq += 1
            return self._msg_seq


@dataclass(slots=True)
class _PutResponse:
    status_code: int
    text: str = ""


def _load_botpy():
    try:
        import botpy
        from botpy.message import C2CMessage, GroupMessage
    except Exception as exc:
        raise SystemExit("Install QQ support with: pip install -e .[gateway]") from exc
    return botpy, C2CMessage, GroupMessage


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _fallback_tag(file_type: int) -> str:
    if file_type == 1:
        return "IMAGE"
    if file_type == 2:
        return "VIDEO"
    return "FILE"


def _build_intents(botpy):
    try:
        return botpy.Intents(public_messages=True, direct_message=True)
    except Exception:
        intents = botpy.Intents.none() if hasattr(botpy.Intents, "none") else botpy.Intents()
        for attr in (
            "public_messages",
            "public_guild_messages",
            "direct_message",
            "direct_messages",
            "c2c_message",
            "c2c_messages",
            "group_at_message",
            "group_at_messages",
        ):
            if hasattr(intents, attr):
                try:
                    setattr(intents, attr, True)
                except Exception:
                    pass
        return intents


def _ext_from_content_type(content_type: str) -> str:
    if not content_type:
        return ""
    ext = mimetypes.guess_extension(content_type.split(";", 1)[0].strip().lower())
    return ext or ""
