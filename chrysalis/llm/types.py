"""LLM 模块核心数据类型。"""

from dataclasses import dataclass, field


@dataclass
class SessionConfig:
    api_key: str
    base_url: str
    model: str
    protocol: str = "openai"
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

    def __post_init__(self):
        self.base_url = self.base_url.rstrip("/")
        self.protocol = self.protocol.strip().lower()
        if not self.name:
            self.name = self.model


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class Response:
    content: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: str = ""
    stop_reason: str = "end_turn"

    @property
    def is_error(self) -> bool:
        return self.content.startswith("!!!Error:")
