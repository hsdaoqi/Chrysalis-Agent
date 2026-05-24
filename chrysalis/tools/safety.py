"""路径安全检查和通用工具函数。"""

import re
from pathlib import Path

from configs.config import PROJECT_ROOT, project_path

SECRET_NAMES = {".env", "id_rsa", "id_ed25519"}
PROJECT_SCOPED_NAMES = {"chrysalis", "data", "memory", "skills", "tests", "workspace", "README.md", "pyproject.toml"}

DANGEROUS_CODE_PATTERNS = (
    "os.popen", "shutil.rmtree", "socket", "subprocess",
)
DANGEROUS_SHELL_PATTERNS = (
    r"\brm\b",
    r"\bdel\b",
    r"\brd\b",
    r"\brmdir\b",
    r"\bremove-item\b",
    r"\bformat\b",
    r"\bshutdown\b",
    r"\brestart-computer\b",
    r"\bstop-process\b",
    r"\btaskkill\b",
    r"\bkill\b",
    r"\bgit\s+reset\s+--hard\b",
)


def safe_path(path: str | Path, workspace: Path | None = None) -> Path:
    raw = Path(path).expanduser()
    if raw.is_absolute():
        resolved = raw.resolve()
    else:
        parts = raw.parts
        if parts and parts[0] in PROJECT_SCOPED_NAMES:
            base = PROJECT_ROOT
        else:
            base = workspace or project_path("workspace")
        resolved = (base / raw).resolve()
    if resolved.name in SECRET_NAMES or resolved.suffix in {".pem", ".key"}:
        raise PermissionError(f"拒绝访问密钥文件: {resolved}")
    return resolved


def blocked_shell_pattern(command: str) -> str | None:
    lowered = command.lower()
    for pattern in DANGEROUS_SHELL_PATTERNS:
        if re.search(pattern, lowered):
            return pattern
    return None


def optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)
