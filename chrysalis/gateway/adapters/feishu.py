"""Feishu/Lark bot adapter using event subscription WebSocket."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from chrysalis.gateway.adapters.base import TextPlatformAdapter, env_bool, env_list, public_access
from chrysalis.gateway.events import MessageEvent, SendResult, SessionSource
from chrysalis.gateway.service import GatewayService
from configs.config import project_path


API_BASE = "https://open.feishu.cn/open-apis"
MEDIA_DIR = project_path("data/gateway/feishu_media")
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

AT_RE = re.compile(r"<at\b[^>]*>.*?</at>", re.I | re.S)


@dataclass
class FeishuConfig:
    app_id: str = field(default_factory=lambda: os.getenv("CHRYSALIS_FEISHU_APP_ID", "").strip())
    app_secret: str = field(default_factory=lambda: os.getenv("CHRYSALIS_FEISHU_APP_SECRET", "").strip())
    verification_token: str = field(default_factory=lambda: os.getenv("CHRYSALIS_FEISHU_VERIFICATION_TOKEN", "").strip())
    encrypt_key: str = field(default_factory=lambda: os.getenv("CHRYSALIS_FEISHU_ENCRYPT_KEY", "").strip())
    bot_open_id: str = field(default_factory=lambda: os.getenv("CHRYSALIS_FEISHU_BOT_OPEN_ID", "").strip())
    api_base: str = field(default_factory=lambda: os.getenv("CHRYSALIS_FEISHU_API_BASE", API_BASE).strip() or API_BASE)
    allowed_users: set[str] = field(default_factory=lambda: env_list("CHRYSALIS_FEISHU_ALLOWED_USERS"))
    allowed_chats: set[str] = field(default_factory=lambda: env_list("CHRYSALIS_FEISHU_ALLOWED_CHATS"))
    allow_all: bool = field(default_factory=lambda: env_bool("CHRYSALIS_FEISHU_ALLOW_ALL", False))
    require_mention: bool = field(default_factory=lambda: env_bool("CHRYSALIS_FEISHU_REQUIRE_MENTION", True))
    trigger_prefixes: set[str] = field(default_factory=lambda: env_list("CHRYSALIS_FEISHU_TRIGGER_PREFIXES"))
    split_limit: int = field(default_factory=lambda: int(os.getenv("CHRYSALIS_FEISHU_SPLIT_LIMIT", "1400")))


class FeishuOpenAPI:
    def __init__(self, config: FeishuConfig) -> None:
        self.config = config
        self._tenant_token = ""
        self._tenant_token_expire_at = 0.0

    def send_message(self, chat_id: str, msg_type: str, content: dict[str, Any]) -> dict[str, Any]:
        return self._post_json(
            "/im/v1/messages",
            {
                "receive_id": chat_id,
                "msg_type": msg_type,
                "content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
            },
            params={"receive_id_type": "chat_id"},
        )

    def upload_image(self, file_path: str) -> str:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(str(path))
        data = self._post_multipart(
            "/im/v1/images",
            data={"image_type": "message"},
            files={"image": (path.name, path.open("rb"), _content_type(path))},
        )
        return str((data.get("data") or {}).get("image_key") or "").strip()

    def upload_file(self, file_path: str) -> str:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(str(path))
        data = self._post_multipart(
            "/im/v1/files",
            data={"file_type": _feishu_file_type(path), "file_name": path.name},
            files={"file": (path.name, path.open("rb"), _content_type(path))},
        )
        return str((data.get("data") or {}).get("file_key") or "").strip()

    def download_resource(
        self,
        *,
        message_id: str,
        file_key: str,
        resource_type: str,
        filename: str = "",
        media_dir: Path = MEDIA_DIR,
    ) -> str:
        if not message_id or not file_key:
            return ""
        requests = _load_requests()
        url = self._url(f"/im/v1/messages/{message_id}/resources/{file_key}")
        response = requests.get(
            url,
            params={"type": resource_type},
            headers={"Authorization": f"Bearer {self._token()}"},
            timeout=60,
        )
        response.raise_for_status()
        name = _safe_filename(filename) or _filename_from_response(response) or f"feishu_{int(time.time() * 1000)}"
        suffix = Path(name).suffix or _suffix_from_content_type(response.headers.get("content-type", ""))
        if suffix and not name.endswith(suffix):
            name += suffix
        media_dir.mkdir(parents=True, exist_ok=True)
        target = media_dir / name
        target.write_bytes(response.content)
        return str(target.resolve())

    def _post_json(
        self,
        path: str,
        body: dict[str, Any],
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        requests = _load_requests()
        response = requests.post(
            self._url(path),
            params=params or {},
            json=body,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        _raise_feishu_error(data)
        return data

    def _post_multipart(
        self,
        path: str,
        *,
        data: dict[str, str],
        files: dict[str, tuple[str, Any, str]],
    ) -> dict[str, Any]:
        requests = _load_requests()
        opened = [item[1] for item in files.values()]
        try:
            response = requests.post(
                self._url(path),
                data=data,
                files=files,
                headers={"Authorization": f"Bearer {self._token()}"},
                timeout=120,
            )
        finally:
            for handle in opened:
                try:
                    handle.close()
                except Exception:
                    pass
        response.raise_for_status()
        payload = response.json()
        _raise_feishu_error(payload)
        return payload

    def _token(self) -> str:
        now = time.time()
        if self._tenant_token and now < self._tenant_token_expire_at - 60:
            return self._tenant_token
        requests = _load_requests()
        response = requests.post(
            self._url("/auth/v3/tenant_access_token/internal"),
            json={"app_id": self.config.app_id, "app_secret": self.config.app_secret},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        _raise_feishu_error(data)
        token = str(data.get("tenant_access_token") or "").strip()
        if not token:
            raise RuntimeError(f"Feishu token response missing tenant_access_token: {data}")
        expire = int(data.get("expire") or 7200)
        self._tenant_token = token
        self._tenant_token_expire_at = now + max(60, expire)
        return token

    def _url(self, path: str) -> str:
        base = self.config.api_base.rstrip("/")
        return f"{base}/{path.lstrip('/')}"


class FeishuAdapter(TextPlatformAdapter):
    label = "Feishu"
    platform = "feishu"

    def __init__(self, service: GatewayService, config: FeishuConfig | None = None) -> None:
        self.service = service
        self.config = config or FeishuConfig()
        self.split_limit = self.config.split_limit
        self.media_dir = MEDIA_DIR
        self.api = FeishuOpenAPI(self.config)

    def run_forever(self) -> None:
        if not self.config.app_id or not self.config.app_secret:
            raise SystemExit("Set CHRYSALIS_FEISHU_APP_ID and CHRYSALIS_FEISHU_APP_SECRET first.")
        lark = _load_lark_oapi()
        delay, max_delay = 3, 120
        while True:
            started_at = time.monotonic()
            try:
                print("[Feishu] connecting event WebSocket...", flush=True)
                handler = self._make_event_handler(lark)
                client_kwargs = {"event_handler": handler}
                log_level = getattr(getattr(lark, "LogLevel", None), "INFO", None)
                if log_level is not None:
                    client_kwargs["log_level"] = log_level
                client = lark.ws.Client(self.config.app_id, self.config.app_secret, **client_kwargs)
                client.start()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"[Feishu] connection error: {exc}", flush=True)
            if time.monotonic() - started_at >= 60:
                delay = 3
            print(f"[Feishu] reconnect in {delay}s...", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, max_delay)

    async def send_text(self, source: SessionSource, text: str) -> SendResult:
        last_id = None
        try:
            for part in self.split_text(text):
                data = await asyncio.to_thread(self.api.send_message, source.chat_id, "text", {"text": part})
                last_id = str((data.get("data") or {}).get("message_id") or "") or last_id
            return SendResult(True, message_id=last_id)
        except Exception as exc:
            print(f"[Feishu] send_text error: {exc}", flush=True)
            return SendResult(False, error=str(exc))

    async def send_image(self, source: SessionSource, file_path: str) -> SendResult:
        path = str(file_path).strip()
        if _is_url(path):
            return await self.send_text(source, f"[IMAGE:{path}]")
        try:
            image_key = await asyncio.to_thread(self.api.upload_image, path)
            if not image_key:
                return SendResult(False, error="Feishu image upload returned no image_key")
            data = await asyncio.to_thread(self.api.send_message, source.chat_id, "image", {"image_key": image_key})
            return SendResult(True, message_id=str((data.get("data") or {}).get("message_id") or ""))
        except Exception as exc:
            print(f"[Feishu] send_image error: {exc}", flush=True)
            return SendResult(False, error=str(exc))

    async def send_file(self, source: SessionSource, file_path: str) -> SendResult:
        path = str(file_path).strip()
        if _is_url(path):
            return await self.send_text(source, f"[FILE:{path}]")
        try:
            file_key = await asyncio.to_thread(self.api.upload_file, path)
            if not file_key:
                return SendResult(False, error="Feishu file upload returned no file_key")
            data = await asyncio.to_thread(self.api.send_message, source.chat_id, "file", {"file_key": file_key})
            return SendResult(True, message_id=str((data.get("data") or {}).get("message_id") or ""))
        except Exception as exc:
            print(f"[Feishu] send_file error: {exc}", flush=True)
            return SendResult(False, error=str(exc))

    async def send_video(self, source: SessionSource, file_path: str) -> SendResult:
        return await self.send_file(source, file_path)

    def _make_event_handler(self, lark: Any) -> Any:
        builder = lark.EventDispatcherHandler.builder(
            self.config.verification_token,
            self.config.encrypt_key,
        )
        register = getattr(builder, "register_p2_im_message_receive_v1", None)
        if register is None:
            raise SystemExit("Installed lark_oapi SDK does not support Feishu message receive events.")
        registered = register(self._handle_receive_event)
        if registered is not None:
            builder = registered
        return builder.build()

    def _handle_receive_event(self, raw_event: Any) -> None:
        event = self._message_event_from_payload(_to_plain(raw_event))
        if event is None:
            return
        print(f"[Feishu] message from {event.source.user_id} ({event.source.chat_type}): {event.text[:80]}", flush=True)
        asyncio.run(self.service.handle_event(self, event))

    def _message_event_from_payload(self, payload: Any) -> MessageEvent | None:
        data = _event_data(payload)
        message = data.get("message") if isinstance(data.get("message"), dict) else {}
        sender = data.get("sender") if isinstance(data.get("sender"), dict) else {}
        sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
        user_id = _first_present(
            sender_id,
            "open_id",
            "user_id",
            "union_id",
        ) or str(sender.get("sender_id") or sender.get("sender_open_id") or "")
        if not user_id:
            user_id = "unknown"

        chat_id = str(message.get("chat_id") or user_id).strip()
        raw_chat_type = str(message.get("chat_type") or "").strip().lower()
        chat_type = "dm" if raw_chat_type in {"p2p", "dm", "single"} else "group"
        if raw_chat_type == "" and chat_id == user_id:
            chat_type = "dm"

        if not public_access(self.config.allowed_users, self.config.allow_all) and user_id not in self.config.allowed_users:
            print(f"[Feishu] unauthorized user: {user_id}", flush=True)
            return None
        if chat_type == "group" and self.config.allowed_chats and chat_id not in self.config.allowed_chats:
            print(f"[Feishu] ignored chat: {chat_id}", flush=True)
            return None

        parsed = self._parse_message(message)
        text, triggered = self._apply_group_trigger(
            parsed["text"],
            mentioned=parsed["mentioned"],
            mentions=parsed["mentions"],
            is_group=chat_type == "group",
        )
        media_paths = self._download_message_media(message, parsed["media"])
        if chat_type == "group" and not triggered:
            return None
        if not text and not media_paths:
            return None

        source = SessionSource(
            platform=self.platform,
            chat_id=chat_id,
            chat_type=chat_type,
            user_id=user_id,
            user_name=str(sender.get("sender_name") or sender.get("name") or ""),
            thread_id=str(message.get("thread_id") or message.get("parent_id") or "") or None,
            message_id=str(message.get("message_id") or "") or None,
            metadata={
                "chat_type": raw_chat_type,
                "message_type": message.get("message_type"),
                "tenant_key": sender.get("tenant_key") or data.get("tenant_key"),
            },
        )
        return MessageEvent(text=text, source=source, raw_message=data, media_paths=media_paths)

    def _parse_message(self, message: dict[str, Any]) -> dict[str, Any]:
        message_type = str(message.get("message_type") or "").strip().lower()
        content = _parse_content(message.get("content"))
        mentions = message.get("mentions") if isinstance(message.get("mentions"), list) else []
        text = ""
        media: list[tuple[str, str, str]] = []

        if message_type == "text":
            text = str(content.get("text") or "")
        elif message_type == "post":
            text = _extract_post_text(content)
        elif message_type == "image":
            key = str(content.get("image_key") or "").strip()
            if key:
                media.append(("image", key, ""))
        elif message_type in {"file", "audio", "media", "video"}:
            key = str(content.get("file_key") or content.get("media_key") or "").strip()
            name = str(content.get("file_name") or content.get("name") or "").strip()
            if key:
                media.append(("file", key, name))
        else:
            text = _extract_post_text(content) or str(content.get("text") or "")

        return {
            "text": _strip_feishu_mentions(text).strip(),
            "mentioned": bool(mentions) or "<at" in text.lower(),
            "mentions": mentions,
            "media": media,
        }

    def _apply_group_trigger(
        self,
        text: str,
        *,
        mentioned: bool,
        mentions: list[Any],
        is_group: bool,
    ) -> tuple[str, bool]:
        if not is_group:
            return text.strip(), True
        stripped = text.strip()
        for prefix in sorted(self.config.trigger_prefixes, key=len, reverse=True):
            if prefix and stripped.startswith(prefix):
                return stripped[len(prefix):].strip(), True
        if not self.config.require_mention:
            return stripped, True
        if self.config.bot_open_id:
            return stripped, _mentions_bot(mentions, self.config.bot_open_id)
        return stripped, mentioned

    def _download_message_media(self, message: dict[str, Any], media: list[tuple[str, str, str]]) -> list[str]:
        paths: list[str] = []
        message_id = str(message.get("message_id") or "").strip()
        for resource_type, file_key, filename in media:
            try:
                path = self.api.download_resource(
                    message_id=message_id,
                    file_key=file_key,
                    resource_type=resource_type,
                    filename=filename,
                    media_dir=self.media_dir,
                )
                if path:
                    paths.append(path)
            except Exception as exc:
                print(f"[Feishu] media download failed: {exc}", flush=True)
        return paths


def _event_data(payload: Any) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    if isinstance(data.get("event"), dict):
        return data["event"]
    return data


def _parse_content(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {"text": raw}
    return {}


def _extract_post_text(content: dict[str, Any]) -> str:
    if "content" not in content:
        for lang in ("zh_cn", "en_us", "ja_jp"):
            localized = content.get(lang)
            if isinstance(localized, dict):
                content = localized
                break
        else:
            for value in content.values():
                if isinstance(value, dict) and "content" in value:
                    content = value
                    break
    parts: list[str] = []
    blocks = content.get("content")
    if isinstance(blocks, list):
        for row in blocks:
            if not isinstance(row, list):
                continue
            for item in row:
                if not isinstance(item, dict):
                    continue
                if item.get("tag") == "text":
                    parts.append(str(item.get("text") or ""))
                elif item.get("tag") == "at":
                    continue
                elif item.get("text"):
                    parts.append(str(item.get("text")))
    title = str(content.get("title") or "").strip()
    body = " ".join(part.strip() for part in parts if part.strip()).strip()
    return "\n".join(part for part in (title, body) if part)


def _strip_feishu_mentions(text: str) -> str:
    cleaned = AT_RE.sub("", text or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t\f\v]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return "\n".join(line.strip() for line in cleaned.splitlines()).strip()


def _mentions_bot(mentions: list[Any], bot_open_id: str) -> bool:
    if not bot_open_id:
        return False
    needle = str(bot_open_id).strip()
    for mention in mentions:
        item = _to_plain(mention)
        values = _flatten_values(item)
        if needle in values:
            return True
    return False


def _flatten_values(value: Any) -> set[str]:
    if isinstance(value, dict):
        result: set[str] = set()
        for item in value.values():
            result.update(_flatten_values(item))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for item in value:
            result.update(_flatten_values(item))
        return result
    text = str(value or "").strip()
    return {text} if text else set()


def _to_plain(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_plain(item) for item in value]
    for method_name in ("to_dict", "model_dump"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                return _to_plain(method())
            except Exception:
                pass
    if hasattr(value, "__dict__"):
        return {
            key: _to_plain(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)


def _first_present(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def _raise_feishu_error(data: dict[str, Any]) -> None:
    code = data.get("code")
    if code in (None, 0):
        return
    msg = data.get("msg") or data.get("message") or data
    raise RuntimeError(f"Feishu API error {code}: {msg}")


def _content_type(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def _feishu_file_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
        return "mp4"
    if suffix in {".opus", ".mp3", ".wav", ".m4a"}:
        return "opus"
    return "stream"


def _safe_filename(value: str) -> str:
    name = Path(str(value or "").strip()).name
    return re.sub(r"[^\w .@()-]+", "_", name).strip(" ._")


def _filename_from_response(response: Any) -> str:
    disposition = str(response.headers.get("content-disposition", "") or "")
    match = re.search(r'filename="?([^";]+)"?', disposition)
    return _safe_filename(match.group(1)) if match else ""


def _suffix_from_content_type(content_type: str) -> str:
    suffix = mimetypes.guess_extension(content_type.split(";", 1)[0].strip().lower())
    return suffix or ""


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _load_requests():
    try:
        import requests
    except Exception as exc:
        raise SystemExit("Install Feishu support with: pip install -e .[gateway]") from exc
    return requests


def _load_lark_oapi():
    try:
        import lark_oapi
    except Exception as exc:
        raise SystemExit("Install Feishu support with: pip install -e .[gateway]") from exc
    return lark_oapi
