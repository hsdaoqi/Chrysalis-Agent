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
    api_key: str = field(default_factory=lambda: os.getenv("CHRYSALIS_API_KEY", ""))
    base_url: str = field(default_factory=lambda: os.getenv("CHRYSALIS_BASE_URL", "https://api.deepseek.com/v1"))
    model: str = field(default_factory=lambda: os.getenv("CHRYSALIS_MODEL", "deepseek-v4-pro"))
    temperature: float = field(default_factory=lambda: float(os.getenv("CHRYSALIS_TEMPERATURE", "0.2")))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("CHRYSALIS_MAX_TOKENS", "4096")))
    timeout: float = field(default_factory=lambda: float(os.getenv("CHRYSALIS_API_TIMEOUT", "60")))


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
    def trace_log(self) -> Path:
        return self.data_dir / "trace.log"
