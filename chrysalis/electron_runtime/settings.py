"""SettingsMixin：拆分自 electron_runtime.py（方法体逐字符保留）。"""
from __future__ import annotations

from chrysalis.electron_runtime._common import *  # noqa: F401,F403


class SettingsMixin:
    def _settings_text(self) -> str:
        payload = {
            "enabled": bool(self._settings.get("enabled", False)),
            "permission_level": _normalize_permission_level(self._settings.get("permission_level")),
            "llm": copy.deepcopy(self._settings.get("llm", {})),
            "system_prompt": str(self._settings.get("system_prompt") or ""),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _load_settings(self) -> dict[str, Any]:
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._default_settings()
        return self._normalize_settings(data if isinstance(data, dict) else {})

    def _default_settings(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "permission_level": self._default_permission_level,
            "llm": {},
            "system_prompt": "",
        }

    def _normalize_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "enabled": bool(data.get("enabled", False)),
            "permission_level": _normalize_permission_level(
                data.get("permission_level") or self._default_permission_level,
            ),
            "llm": self._normalize_llm_settings(data.get("llm", {})),
            "system_prompt": str(data.get("system_prompt") or ""),
        }

    def _normalize_llm_settings(self, data: Any) -> dict[str, Any]:
        llm = data if isinstance(data, dict) else {}
        return {
            "name": str(llm.get("name") or ""),
            "provider": str(llm.get("provider") or "openai"),
            "api_key": str(llm.get("api_key") or ""),
            "base_url": str(llm.get("base_url") or ""),
            "model": str(llm.get("model") or ""),
            "wire_api": str(llm.get("wire_api") or "chat"),
            "context_window": self._to_int(llm.get("context_window"), 28000),
            "temperature": self._to_float(llm.get("temperature"), 0.2),
            "max_tokens": self._to_optional_int(llm.get("max_tokens")),
            "max_retries": self._to_int(llm.get("max_retries"), 4),
            "timeout": self._to_int(llm.get("timeout"), 60),
            "proxy": str(llm.get("proxy") or ""),
            "thinking": str(llm.get("thinking") or "disabled"),
            "thinking_budget": self._to_optional_int(llm.get("thinking_budget")),
        }

    def _to_int(self, value: Any, fallback: int) -> int:
        try:
            if value in (None, ""):
                return fallback
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def _to_optional_int(self, value: Any) -> int | None:
        try:
            if value in (None, ""):
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _to_float(self, value: Any, fallback: float) -> float:
        try:
            if value in (None, ""):
                return fallback
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def save_settings_text(self, raw: str) -> bool:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return False
        if not isinstance(data, dict):
            return False
        self._settings = self._normalize_settings(data)
        self._save_settings()
        self._reload_kernel()
        self._refresh_workspace_state(emit=False)
        return True

    def reset_settings(self) -> None:
        self._settings = self._default_settings()
        self._save_settings()
        self._reload_kernel()
        self._refresh_workspace_state(emit=False)

    def set_permission_level(self, level: str) -> bool:
        normalized = _normalize_permission_level(level, fallback="")
        if normalized not in {"locked", "balanced", "full"}:
            return False
        self._settings["permission_level"] = normalized
        self._save_settings()
        self._apply_settings_to_kernel(self.kernel)
        return True

    def _apply_settings_to_kernel(self, kernel: Kernel) -> None:
        kernel.set_permission_level(_normalize_permission_level(self._settings.get("permission_level")))

    def _save_settings(self) -> None:
        try:
            self._settings_path.write_text(json.dumps(self._settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

