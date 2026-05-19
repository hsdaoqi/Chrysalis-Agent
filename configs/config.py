"""项目路径和运行时配置。"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def project_path(path: str | Path) -> Path:
    """把相对路径解析到项目主目录，而不是解析到当前 shell 目录。"""
    value = Path(path).expanduser()
    if value.is_absolute():
        return value.resolve()
    return (PROJECT_ROOT / value).resolve()


@dataclass
class LLMConfig:
    provider: str = field(default_factory=lambda: os.getenv("CHRYSALIS_LLM_PROVIDER", "deepseek"))
    api_key: str = field(default_factory=lambda: os.getenv("CHRYSALIS_API_KEY", ""))
    base_url: str = field(default_factory=lambda: os.getenv("CHRYSALIS_BASE_URL", ""))
    model: str = field(default_factory=lambda: os.getenv("CHRYSALIS_MODEL", ""))
    temperature: float = field(default_factory=lambda: float(os.getenv("CHRYSALIS_TEMPERATURE", "0.2")))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("CHRYSALIS_MAX_TOKENS", "4096")))
    timeout: float = field(default_factory=lambda: float(os.getenv("CHRYSALIS_API_TIMEOUT", "60")))
    context_window: int = field(default_factory=lambda: int(os.getenv("CHRYSALIS_CONTEXT_WINDOW", "28000")))
    max_retries: int = field(default_factory=lambda: int(os.getenv("CHRYSALIS_MAX_RETRIES", "4")))
    proxy: str = field(default_factory=lambda: os.getenv("CHRYSALIS_PROXY", ""))
    input_price: float = field(default_factory=lambda: float(os.getenv("CHRYSALIS_INPUT_PRICE", "0")))
    output_price: float = field(default_factory=lambda: float(os.getenv("CHRYSALIS_OUTPUT_PRICE", "0")))
    cache_read_price: float = field(default_factory=lambda: float(os.getenv("CHRYSALIS_CACHE_READ_PRICE", "0")))
    cache_write_price: float = field(default_factory=lambda: float(os.getenv("CHRYSALIS_CACHE_WRITE_PRICE", "0")))

    def __post_init__(self) -> None:
        self.provider = (self.provider or "deepseek").strip().lower()
        if not self.base_url:
            self.base_url = _default_llm_base_url(self.provider)
        self.base_url = _normalize_llm_base_url(self.provider, self.base_url)
        if not self.model:
            self.model = _default_llm_model(self.provider)

    def to_session_config(self):
        """转换为新 LLM 模块的 SessionConfig。"""
        from chrysalis.llm.types import SessionConfig
        protocol = "anthropic" if self.provider in {"anthropic", "claude"} else "openai"
        return SessionConfig(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            protocol=protocol,
            context_window=self.context_window,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            max_retries=self.max_retries,
            read_timeout=int(self.timeout),
            proxy=self.proxy or None,
            name=self.model,
        )

    def pricing_dict(self) -> dict[str, float] | None:
        """返回每百万 token 的价格配置，全为 0 时返回 None（使用内置默认值）。"""
        if not any([self.input_price, self.output_price, self.cache_read_price, self.cache_write_price]):
            return None
        return {
            "input": self.input_price,
            "output": self.output_price,
            "cache_read": self.cache_read_price,
            "cache_write": self.cache_write_price,
        }


def _default_llm_provider() -> str:
    return os.getenv("CHRYSALIS_LLM_PROVIDER", "deepseek").strip().lower()


def _default_llm_base_url(provider: str | None = None) -> str:
    provider = (provider or _default_llm_provider()).strip().lower()
    if provider == "openai":
        return "https://api.openai.com/v1"
    if provider in {"deepseek", "ds"}:
        return "https://api.deepseek.com"
    return ""


def _default_llm_model(provider: str | None = None) -> str:
    provider = (provider or _default_llm_provider()).strip().lower()
    if provider == "openai":
        return "gpt-4.1-mini"
    return "deepseek-v4-pro"


def _normalize_llm_base_url(provider: str, base_url: str) -> str:
    value = (base_url or "").strip().rstrip("/")
    if provider in {"deepseek", "ds"} and value == "https://api.deepseek.com":
        return value
    if provider == "openai" and value == "https://api.openai.com":
        return value + "/v1"
    return value


@dataclass
class AgentConfig:
    """除非显式传入绝对路径，否则所有长期状态都放在项目主目录下。"""

    root: Path = PROJECT_ROOT
    llm: LLMConfig = field(default_factory=LLMConfig)
    skills_dir: Path = field(default_factory=lambda: project_path("skills"))
    data_dir: Path = field(default_factory=lambda: project_path("data"))
    memory_dir: Path = field(default_factory=lambda: project_path("memory"))
    workspace_dir: Path = field(default_factory=lambda: project_path("workspace"))
    max_turns: int = field(default_factory=lambda: int(os.getenv("CHRYSALIS_MAX_TURNS", "40")))
    min_skill_turns: int = field(default_factory=lambda: int(os.getenv("CHRYSALIS_MIN_SKILL_TURNS", "16")))

    def __post_init__(self) -> None:
        self.skills_dir = project_path(self.skills_dir)
        self.data_dir = project_path(self.data_dir)
        self.memory_dir = project_path(self.memory_dir)
        self.workspace_dir = project_path(self.workspace_dir)
        for path in (self.skills_dir, self.data_dir, self.memory_dir, self.workspace_dir):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def memory_json(self) -> Path:
        return self.data_dir / "memory.json"

    @property
    def model_responses_dir(self) -> Path:
        return self.data_dir / "model_responses"

    @property
    def l4_session_dir(self) -> Path:
        return self.data_dir / "l4_session"
