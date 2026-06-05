"""LLM 模块核心数据类型。

History 内部统一使用 canonical block 格式，与具体协议解耦：

    {
        "role": "user" | "assistant" | "system",
        "blocks": [
            {"type": "text", "text": "..."},
            {"type": "thinking", "text": "...", "signature": "..."},
            {"type": "tool_use", "id": "...", "name": "...", "arguments": "{json}"},
            {"type": "tool_result", "tool_use_id": "...", "content": "...", "is_error": False},
        ],
    }

发送给具体 provider 时由 protocols 模块转换为 OpenAI / Anthropic 各自的 wire format。
"""

from dataclasses import dataclass, field


class CancelledError(Exception):
    """Raised when an in-flight LLM request is cancelled cooperatively."""


@dataclass
class SessionConfig:
    api_key: str
    base_url: str
    model: str
    protocol: str = "openai"
    wire_api: str = "chat"
    context_window: int = 28000
    temperature: float = 1.0
    max_tokens: int | None = None
    stream: bool = True
    max_retries: int = 4
    connect_timeout: int = 5
    read_timeout: int = 30
    proxy: str | None = None
    thinking: str = "disabled"
    thinking_budget: int | None = None
    name: str = ""
    compression_enabled: bool = True
    compression_soft_limit_ratio: float = 0.70
    compression_hard_limit_ratio: float = 0.90
    compression_recent_turns: int = 8
    compression_reactive_recent_turns: int = 5
    compression_tail_token_budget: int | None = None
    compression_tool_result_budget: int = 200_000
    compression_max_failures: int = 3
    prompt_cache_enabled: bool = True

    def __post_init__(self):
        self.base_url = self.base_url.rstrip("/")
        self.protocol = self.protocol.strip().lower()
        self.wire_api = (self.wire_api or "chat").strip().lower()
        self.prompt_cache_enabled = _coerce_bool(self.prompt_cache_enabled, True)
        if not self.name:
            self.name = self.model


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_creation_tokens=self.cache_creation_tokens + other.cache_creation_tokens,
        )

    def __iadd__(self, other: "Usage") -> "Usage":
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_creation_tokens += other.cache_creation_tokens
        return self

    def __bool__(self) -> bool:
        return self.total_tokens > 0

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
        }

    @staticmethod
    def from_dict(d: dict) -> "Usage":
        return Usage(
            prompt_tokens=d.get("prompt_tokens", 0),
            completion_tokens=d.get("completion_tokens", 0),
            total_tokens=d.get("total_tokens", 0),
            cache_read_tokens=d.get("cache_read_tokens", 0),
            cache_creation_tokens=d.get("cache_creation_tokens", 0),
        )

    def format(self) -> str:
        parts = [f"{_fmt_num(self.prompt_tokens)}in + {_fmt_num(self.completion_tokens)}out = {_fmt_num(self.total_tokens)}"]
        if self.cache_read_tokens:
            parts.append(f"cache_read: {_fmt_num(self.cache_read_tokens)}")
        if self.cache_creation_tokens:
            parts.append(f"cache_write: {_fmt_num(self.cache_creation_tokens)}")
        return ", ".join(parts)


def _fmt_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}k"
    if n >= 1_000:
        return f"{n / 1_000:.2f}k"
    return str(n)


def _coerce_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Response:
    content: str = ""
    thinking: str = ""
    thinking_signature: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: str = ""
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)
    cancelled: bool = False

    @property
    def is_error(self) -> bool:
        return self.content.startswith("!!!Error:")


# ── canonical block 构造辅助 ──

def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def thinking_block(text: str, signature: str = "") -> dict:
    return {"type": "thinking", "text": text, "signature": signature}


def tool_use_block(tool_id: str, name: str, arguments: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "arguments": arguments}


def image_block(media_type: str, data: str) -> dict:
    return {"type": "image", "media_type": media_type, "data": data}


def tool_result_block(tool_use_id: str, content: str, is_error: bool = False) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


def message(role: str, blocks: list[dict]) -> dict:
    return {"role": role, "blocks": blocks}
