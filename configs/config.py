"""项目路径和运行时配置。"""

import json
import os
import re
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
        provider = self.provider.strip().lower()
        if not self.base_url:
            if provider == "openai":
                self.base_url = "https://api.openai.com/v1"
            elif provider in {"anthropic", "claude"}:
                self.base_url = "https://api.anthropic.com/v1"
            else:
                self.base_url = "https://api.deepseek.com"
        if not self.model:
            if provider == "openai":
                self.model = "gpt-4.1-mini"
            elif provider in {"anthropic", "claude"}:
                self.model = "claude-3-5-sonnet-latest"
            else:
                self.model = "deepseek-chat"

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

    def load_session_configs(self) -> "list":
        """加载多模型配置，返回 SessionConfig 列表。

        优先读取 configs/llm_models.json；不存在则回退到单模型 .env 配置。
        JSON 中的字符串值支持 ${ENV_VAR} 语法引用环境变量。
        """
        from chrysalis.llm.types import SessionConfig

        desktop_config = self._load_desktop_session_config()
        if desktop_config is not None:
            return [desktop_config]

        models_path = PROJECT_ROOT / "configs" / "llm_models.json"
        if not models_path.exists():
            return [self.llm.to_session_config()]

        raw = models_path.read_text(encoding="utf-8")
        models = json.loads(raw)
        if not isinstance(models, list) or not models:
            return [self.llm.to_session_config()]

        configs = []
        for entry in models:
            entry = _expand_env_vars(entry)
            provider = (entry.get("provider") or "openai").strip().lower()
            protocol = "anthropic" if provider in {"anthropic", "claude"} else "openai"
            base_url = entry.get("base_url")
            model = entry.get("model")

            configs.append(SessionConfig(
                api_key=entry.get("api_key", ""),
                base_url=base_url,
                model=model,
                protocol=protocol,
                context_window=int(entry.get("context_window", 28000)),
                temperature=float(entry.get("temperature", 0.2)),
                max_tokens=int(entry.get("max_tokens", 4096)) if entry.get("max_tokens") else None,
                max_retries=int(entry.get("max_retries", 4)),
                connect_timeout=int(entry.get("connect_timeout", 5)),
                read_timeout=int(entry.get("timeout", 60)),
                proxy=entry.get("proxy") or None,
                thinking=entry.get("thinking", "disabled"),
                thinking_budget=int(entry.get("thinking_budget")) if entry.get("thinking_budget") else None,
                name=entry.get("name") or model,
            ))
        return configs

    def _load_desktop_session_config(self):
        from chrysalis.llm.types import SessionConfig

        settings_path = self.data_dir / "desktop_settings.json"
        if not settings_path.exists():
            return None
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or not bool(data.get("enabled", False)):
            return None
        llm = data.get("llm")
        if not isinstance(llm, dict):
            return None
        provider = (llm.get("provider") or "openai").strip().lower()
        protocol = "anthropic" if provider in {"anthropic", "claude"} else "openai"
        model = llm.get("model") or ""
        return SessionConfig(
            api_key=llm.get("api_key", ""),
            base_url=llm.get("base_url", ""),
            model=model,
            protocol=protocol,
            context_window=int(llm.get("context_window", 28000)),
            temperature=float(llm.get("temperature", 0.2)),
            max_tokens=int(llm.get("max_tokens", 4096)) if llm.get("max_tokens") else None,
            max_retries=int(llm.get("max_retries", 4)),
            connect_timeout=int(llm.get("connect_timeout", 5)),
            read_timeout=int(llm.get("timeout", 60)),
            proxy=llm.get("proxy") or None,
            thinking=llm.get("thinking", "disabled"),
            thinking_budget=int(llm.get("thinking_budget")) if llm.get("thinking_budget") else None,
            name=llm.get("name") or model,
        )


def _expand_env_vars(obj):
    """递归展开字典/列表中字符串值里的 ${ENV_VAR} 引用。"""
    if isinstance(obj, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.getenv(m.group(1), ""), obj)
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj
