"""Base helpers for platform adapters."""

from __future__ import annotations

import os
from collections.abc import Iterable

from chrysalis.gateway.events import SendResult, SessionSource, split_text


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "all", "*"}


def env_list(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {part.strip() for part in raw.replace(";", ",").split(",") if part.strip()}


def public_access(allowed_users: Iterable[str], allow_all: bool = False) -> bool:
    values = {str(item).strip() for item in allowed_users if str(item).strip()}
    return allow_all or not values or "*" in values


class TextPlatformAdapter:
    label = "Gateway"
    platform = "gateway"
    split_limit = 1500

    async def send_text(self, source: SessionSource, text: str) -> SendResult:
        raise NotImplementedError

    async def send_image(self, source: SessionSource, file_path: str) -> SendResult:
        return await self.send_text(source, f"[IMAGE:{file_path}]")

    async def send_file(self, source: SessionSource, file_path: str) -> SendResult:
        return await self.send_text(source, f"[FILE:{file_path}]")

    async def send_video(self, source: SessionSource, file_path: str) -> SendResult:
        return await self.send_file(source, file_path)

    def split_text(self, text: str) -> list[str]:
        return split_text(text, self.split_limit)
