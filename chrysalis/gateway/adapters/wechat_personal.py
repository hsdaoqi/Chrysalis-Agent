"""Experimental personal WeChat adapter based on the iLink bot endpoints."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import struct
import sys
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from chrysalis.gateway.adapters.base import TextPlatformAdapter, env_bool, env_list, public_access
from chrysalis.gateway.events import MessageEvent, SendResult, SessionSource
from chrysalis.gateway.service import GatewayService
from configs.config import project_path


API = "https://ilinkai.weixin.qq.com"
VERSION = "2.1.10"
MSG_USER = 1
MSG_BOT = 2
ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_FILE = 4
ITEM_VIDEO = 5
STATE_FINISH = 2
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (1 << 8) | 10
USER_AGENT = f"chrysalis-weixin/{VERSION}"
CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"


@dataclass
class WeChatPersonalConfig:
    token_file: Path = field(
        default_factory=lambda: Path(
            os.getenv("CHRYSALIS_WECHAT_TOKEN_FILE", "")
            or project_path("data/gateway/wechat_personal_token.json")
        )
    )
    allowed_users: set[str] = field(default_factory=lambda: env_list("CHRYSALIS_WECHAT_ALLOWED_USERS"))
    allow_all: bool = field(default_factory=lambda: env_bool("CHRYSALIS_WECHAT_ALLOW_ALL", False))
    split_limit: int = field(default_factory=lambda: int(os.getenv("CHRYSALIS_WECHAT_SPLIT_LIMIT", "1200")))


class WxBotClient:
    def __init__(self, token_file: Path):
        self.token_file = token_file.expanduser().resolve()
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token = ""
        self.bot_id = ""
        self._updates_buf = ""
        self._load()

    def _load(self) -> None:
        if not self.token_file.exists():
            return
        data = json.loads(self.token_file.read_text(encoding="utf-8"))
        self.token = data.get("bot_token", "")
        self.bot_id = data.get("ilink_bot_id", "")
        self._updates_buf = data.get("updates_buf", "")

    def _save(self, **extra: Any) -> None:
        data = {
            "bot_token": self.token or "",
            "ilink_bot_id": self.bot_id or "",
            "updates_buf": self._updates_buf or "",
            **extra,
        }
        self.token_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def login_qr(self, poll_interval: int = 2) -> None:
        requests, qrcode = _load_qrcode_requests()
        response = requests.get(
            f"{API}/ilink/bot/get_bot_qrcode",
            params={"bot_type": 3},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        qr_id = data["qrcode"]
        url = data.get("qrcode_img_content", "")
        print(f"[WeChat] QR login id: {qr_id}", flush=True)
        if url:
            image_path = self.token_file.parent / "wechat_qr.png"
            qrcode.make(url).save(str(image_path))
            webbrowser.open(str(image_path))
            qr = qrcode.QRCode(border=1)
            qr.add_data(url)
            qr.make(fit=True)
            qr.print_ascii(invert=True)
        last_status = ""
        while True:
            time.sleep(poll_interval)
            try:
                status = requests.get(
                    f"{API}/ilink/bot/get_qrcode_status",
                    params={"qrcode": qr_id},
                    headers={"User-Agent": USER_AGENT},
                    timeout=60,
                ).json()
            except requests.exceptions.ReadTimeout:
                continue
            state = status.get("status", "")
            if state != last_status:
                print(f"[WeChat] QR status: {state}", flush=True)
                last_status = state
            if state == "confirmed":
                self.token = status.get("bot_token", "")
                self.bot_id = status.get("ilink_bot_id", "")
                self._save(login_time=time.strftime("%Y-%m-%d %H:%M:%S"))
                print(f"[WeChat] login ok: bot_id={self.bot_id}", flush=True)
                return
            if state == "expired":
                raise RuntimeError("WeChat QR code expired")

    def get_updates(self, timeout: int = 30) -> list[dict[str, Any]]:
        requests = _load_requests()
        try:
            response = self._post(
                "ilink/bot/getupdates",
                {
                    "get_updates_buf": self._updates_buf or "",
                    "base_info": {"channel_version": VERSION},
                },
                timeout=timeout + 5,
            )
        except requests.exceptions.ReadTimeout:
            return []
        if response.get("errcode"):
            print(f"[WeChat] getUpdates error: {response.get('errcode')} {response.get('errmsg', '')}", flush=True)
            if response.get("errcode") == -14:
                self._updates_buf = ""
                self._save()
            return []
        next_buf = response.get("get_updates_buf", "")
        if next_buf:
            self._updates_buf = next_buf
            self._save()
        return response.get("msgs") or []

    def send_text(self, to_user_id: str, text: str, context_token: str = "") -> Any:
        msg = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": f"pyclient-{uuid.uuid4().hex[:16]}",
            "message_type": MSG_BOT,
            "message_state": STATE_FINISH,
            "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
        }
        if context_token:
            msg["context_token"] = context_token
        return self._post("ilink/bot/sendmessage", {"msg": msg, "base_info": {"channel_version": VERSION}})

    def send_image(self, to_user_id: str, file_path: str, context_token: str = "") -> Any:
        return self._send_media(to_user_id, file_path, item_key="image_item", media_type=1, context_token=context_token)

    def send_file(self, to_user_id: str, file_path: str, context_token: str = "") -> Any:
        return self._send_media(to_user_id, file_path, item_key="file_item", media_type=3, context_token=context_token)

    def send_video(self, to_user_id: str, file_path: str, context_token: str = "") -> Any:
        return self._send_media(to_user_id, file_path, item_key="video_item", media_type=2, context_token=context_token)

    def _send_media(self, to_user_id: str, file_path: str, *, item_key: str, media_type: int, context_token: str = "") -> Any:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(str(path))

        item_type = {
            "image_item": ITEM_IMAGE,
            "file_item": ITEM_FILE,
            "video_item": ITEM_VIDEO,
        }.get(item_key, ITEM_FILE)

        raw = path.read_bytes()
        filekey = uuid.uuid4().hex
        aes_key = os.urandom(16)
        ciphertext_size = ((len(raw) // 16) + 1) * 16
        thumb_raw = b""
        thumb_w = thumb_h = 0
        thumb_ciphertext_size = 0

        if item_key == "image_item":
            from io import BytesIO

            from PIL import Image

            im = Image.open(path)
            im.thumbnail((240, 240))
            thumb_w, thumb_h = im.size
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            bio = BytesIO()
            im.save(bio, format="JPEG", quality=85)
            thumb_raw = bio.getvalue()
            thumb_ciphertext_size = ((len(thumb_raw) // 16) + 1) * 16

        body = {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": len(raw),
            "rawfilemd5": hashlib.md5(raw).hexdigest(),
            "filesize": ciphertext_size,
            "no_need_thumb": item_key not in ("image_item", "video_item"),
            "aeskey": aes_key.hex(),
            "base_info": {"channel_version": VERSION},
        }
        if thumb_raw:
            body.update({
                "thumb_rawsize": len(thumb_raw),
                "thumb_rawfilemd5": hashlib.md5(thumb_raw).hexdigest(),
                "thumb_filesize": thumb_ciphertext_size,
            })

        resp = self._post("ilink/bot/getuploadurl", body)
        upload_param = resp.get("upload_param", "")
        upload_url = resp.get("upload_full_url", "")
        if not (upload_param or upload_url):
            raise RuntimeError(f"getuploadurl failed: {resp}")

        media = self._upload(filekey, upload_param, raw, aes_key=aes_key, upload_url=upload_url)
        item = {"media": media}
        if item_key == "file_item":
            item.update({"file_name": path.name, "len": str(len(raw))})
        elif item_key == "image_item":
            thumb_param = resp.get("thumb_upload_param", "")
            thumb_url = resp.get("thumb_upload_full_url", "")
            if thumb_param or thumb_url:
                thumb_media = self._upload(filekey, thumb_param, thumb_raw, aes_key=aes_key, upload_url=thumb_url)
                thumb_size = thumb_ciphertext_size
            else:
                thumb_media = media
                thumb_size = ciphertext_size
            item.update({
                "mid_size": ciphertext_size,
                "thumb_media": thumb_media,
                "thumb_size": thumb_size,
                "thumb_width": thumb_w,
                "thumb_height": thumb_h,
            })
        elif item_key == "video_item":
            item.update({"video_size": ciphertext_size})

        msg = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": f"pyclient-{uuid.uuid4().hex[:16]}",
            "message_type": MSG_BOT,
            "message_state": STATE_FINISH,
            "item_list": [{"type": item_type, item_key: item}],
        }
        if context_token:
            msg["context_token"] = context_token
        return self._post("ilink/bot/sendmessage", {"msg": msg, "base_info": {"channel_version": VERSION}})

    def _upload(self, filekey: str, upload_param: str, raw: bytes, *, aes_key: bytes, upload_url: str = "", timeout: int = 120) -> dict[str, Any]:
        requests = _load_requests()
        from Crypto.Cipher import AES

        url = upload_url.strip() if upload_url else f"{CDN_BASE}/upload?encrypted_query_param={quote(upload_param)}&filekey={filekey}"
        data = self._enc(raw, aes_key, AES)
        last_err = None
        for attempt in range(1, 4):
            try:
                response = requests.post(url, data=data, headers={"Content-Type": "application/octet-stream", "User-Agent": USER_AGENT}, timeout=timeout)
                if 400 <= response.status_code < 500:
                    msg = response.headers.get("x-error-message") or response.text[:300]
                    raise RuntimeError(f"CDN upload client error {response.status_code}: {msg}")
                if response.status_code != 200:
                    msg = response.headers.get("x-error-message") or f"status {response.status_code}"
                    raise RuntimeError(f"CDN upload server error: {msg}")
                encrypted = response.headers.get("x-encrypted-param", "")
                if not encrypted:
                    raise RuntimeError("CDN upload response missing x-encrypted-param header")
                return {
                    "encrypt_query_param": encrypted,
                    "aes_key": base64.b64encode(aes_key.hex().encode()).decode(),
                    "encrypt_type": 1,
                }
            except Exception as exc:
                last_err = exc
                if "client error" in str(exc) or attempt >= 3:
                    break
                print(f"[WeChat] CDN upload retry {attempt}: {exc}", file=sys.__stdout__)
        raise last_err

    @staticmethod
    def _enc(raw: bytes, aes_key: bytes, aes_cls) -> bytes:
        pad = 16 - (len(raw) % 16)
        return aes_cls.new(aes_key, aes_cls.MODE_ECB).encrypt(raw + bytes([pad] * pad))

    def run_loop(self, on_message: Callable[["WxBotClient", dict[str, Any]], None], poll_timeout: int = 30) -> None:
        print(f"[WeChat] listening... bot_id={self.bot_id}", flush=True)
        seen: list[str] = []
        while True:
            try:
                for msg in self.get_updates(poll_timeout):
                    message_id = str(msg.get("message_id", "") or "")
                    if not self.is_user_msg(msg) or message_id in seen:
                        continue
                    if message_id:
                        seen.append(message_id)
                        if len(seen) > 5000:
                            del seen[:3000]
                    on_message(self, msg)
            except KeyboardInterrupt:
                print("[WeChat] exit", flush=True)
                break
            except Exception as exc:
                print(f"[WeChat] loop error: {exc}; retrying in 5s", flush=True)
                time.sleep(5)

    def _post(self, endpoint: str, body: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
        requests = _load_requests()
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Content-Length": str(len(data)),
            "X-WECHAT-UIN": _uin(),
            "iLink-App-Id": ILINK_APP_ID,
            "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
            "User-Agent": USER_AGENT,
        }
        if self.token.strip():
            headers["Authorization"] = f"Bearer {self.token.strip()}"
        response = requests.post(f"{API}/{endpoint}", data=data, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def extract_text(msg: dict[str, Any]) -> str:
        return "\n".join(
            item["text_item"].get("text", "")
            for item in msg.get("item_list", [])
            if item.get("type") == ITEM_TEXT and item.get("text_item")
        )

    @staticmethod
    def extract_media(items: list[dict[str, Any]]) -> list[str]:
        paths: list[str] = []
        if not items:
            return paths
        requests = _load_requests()
        from Crypto.Cipher import AES

        media_keys = {
            "image_item": ".jpg",
            "video_item": ".mp4",
            "file_item": "",
            "voice_item": ".silk",
        }
        for item in items:
            for key, ext in media_keys.items():
                sub = item.get(key) if isinstance(item, dict) else None
                if not sub:
                    continue
                media = sub.get("media") or {}
                encrypted_param = media.get("encrypt_query_param")
                if not encrypted_param:
                    continue
                aes_key_field = media.get("aes_key", "") or sub.get("aeskey", "")
                if not aes_key_field:
                    continue
                try:
                    if media.get("aes_key"):
                        aes_key = bytes.fromhex(base64.b64decode(aes_key_field).decode())
                    else:
                        aes_key = bytes.fromhex(aes_key_field)
                    content = requests.get(f"{CDN_BASE}/download?encrypted_query_param={quote(encrypted_param)}", headers={"User-Agent": USER_AGENT}, timeout=60).content
                    plain = AES.new(aes_key, AES.MODE_ECB).decrypt(content)
                    plain = plain[:-plain[-1]]
                    filename = sub.get("file_name") or f"{uuid.uuid4().hex[:8]}{ext or '.bin'}"
                    target_dir = project_path("data/gateway/wechat_media")
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target = target_dir / Path(filename).name
                    target.write_bytes(plain)
                    paths.append(str(target.resolve()))
                except Exception as exc:
                    print(f"[WeChat] media download failed ({key}): {exc}", file=sys.__stdout__)
                break
        return paths

    @staticmethod
    def is_user_msg(msg: dict[str, Any]) -> bool:
        return msg.get("message_type") == MSG_USER


class WeChatPersonalAdapter(TextPlatformAdapter):
    label = "Personal WeChat"
    platform = "wechat_personal"

    def __init__(self, service: GatewayService, config: WeChatPersonalConfig | None = None) -> None:
        self.service = service
        self.config = config or WeChatPersonalConfig()
        self.split_limit = self.config.split_limit
        self.client = WxBotClient(self.config.token_file)

    def run_forever(self) -> None:
        if not self.client.token:
            self.client.login_qr()
        self.client.run_loop(self._on_message)

    async def send_text(self, source: SessionSource, text: str) -> SendResult:
        if not self.client:
            return SendResult(False, error="WeChat client is not ready")
        try:
            context_token = str(source.metadata.get("context_token", "") or "")
            raw_response = None
            for part in self.split_text(text):
                raw_response = await asyncio.to_thread(self.client.send_text, source.chat_id, part, context_token)
            return SendResult(True, raw_response=raw_response)
        except Exception as exc:
            print(f"[WeChat] send_text error: {exc}", flush=True)
            return SendResult(False, error=str(exc))

    async def send_image(self, source: SessionSource, file_path: str) -> SendResult:
        return await self._send_media(source, file_path, item_key="image_item", media_type=1)

    async def send_file(self, source: SessionSource, file_path: str) -> SendResult:
        return await self._send_media(source, file_path, item_key="file_item", media_type=3)

    async def send_video(self, source: SessionSource, file_path: str) -> SendResult:
        return await self._send_media(source, file_path, item_key="video_item", media_type=2)

    async def _send_media(self, source: SessionSource, file_path: str, *, item_key: str, media_type: int) -> SendResult:
        if not self.client:
            return SendResult(False, error="WeChat client is not ready")
        try:
            context_token = str(source.metadata.get("context_token", "") or "")
            sender = {
                "image_item": self.client.send_image,
                "file_item": self.client.send_file,
                "video_item": self.client.send_video,
            }.get(item_key, self.client.send_file)
            raw_response = await asyncio.to_thread(sender, source.chat_id, file_path, context_token)
            return SendResult(True, raw_response=raw_response)
        except Exception as exc:
            print(f"[WeChat] send media error: {exc}", flush=True)
            return SendResult(False, error=str(exc))

    def _on_message(self, _bot: WxBotClient, msg: dict[str, Any]) -> None:
        text = self.client.extract_text(msg).strip()
        media_paths = self.client.extract_media(msg.get("item_list", []) or [])
        user_id = str(msg.get("from_user_id") or msg.get("sender") or "")
        if not public_access(self.config.allowed_users, self.config.allow_all) and user_id not in self.config.allowed_users:
            print(f"[WeChat] unauthorized user: {user_id}", flush=True)
            return
        if not text and not media_paths:
            return
        source = SessionSource(
            platform=self.platform,
            chat_id=user_id,
            chat_type="dm",
            user_id=user_id,
            message_id=str(msg.get("message_id", "") or "") or None,
            metadata={"context_token": msg.get("context_token", "")},
        )
        event = MessageEvent(text=text, source=source, raw_message=msg, media_paths=media_paths)
        print(f"[WeChat] message from {user_id}: {text[:80]}", flush=True)
        asyncio.run(self.service.handle_event(self, event))


def _uin() -> str:
    return base64.b64encode(str(struct.unpack(">I", os.urandom(4))[0]).encode()).decode()


def _load_requests():
    try:
        import requests
    except Exception as exc:
        raise SystemExit("Install WeChat support with: pip install -e .[gateway]") from exc
    return requests


def _load_qrcode_requests():
    try:
        import qrcode
        import requests
    except Exception as exc:
        raise SystemExit("Install WeChat support with: pip install -e .[gateway]") from exc
    return requests, qrcode
