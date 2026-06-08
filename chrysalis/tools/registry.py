"""工具注册表：@tool 装饰器 + 统一分发 + TOOL_PROMPT 自动生成。"""

from dataclasses import dataclass, field
import inspect
from pathlib import Path

from configs.config import PROJECT_ROOT


@dataclass
class ToolDef:
    name: str
    description: str
    params: dict[str, str]
    fn: object

_REGISTRY: dict[str, ToolDef] = {}

_ALIASES: dict[str, str] = {}


def tool(name: str, description: str, params: dict[str, str] | None = None):
    """装饰器：注册一个工具函数。函数签名必须是 (args: dict, workspace=None) -> dict。"""
    def decorator(fn):
        _REGISTRY[name] = ToolDef(name=name, description=description, params=params or {}, fn=fn)
        return fn
    return decorator


def run_tool(name: str, args: dict, workspace: Path | None = None, on_stream=None) -> dict:
    """统一分发入口。

    若工具函数签名声明了 on_stream 参数且调用方传入了 on_stream 回调，则透传，
    以便工具在执行期间边跑边回传输出（流式）。否则保持旧行为，向后兼容。
    """
    name, args = _normalize_alias_call(name, args)
    tool_def = _REGISTRY.get(name)
    if not tool_def:
        return {"ok": False, "error": f"未知工具: {name}"}
    try:
        if on_stream is not None and _tool_accepts_on_stream(tool_def.fn):
            return tool_def.fn(args=args, workspace=workspace, on_stream=on_stream)
        return tool_def.fn(args=args, workspace=workspace)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _tool_accepts_on_stream(fn) -> bool:
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "on_stream" in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def _normalize_alias_call(name: str, args: dict) -> tuple[str, dict]:
    target = _ALIASES.get(name)
    if not target:
        return name, args
    return target, dict(args or {})


def generate_tool_prompt(exclude: set[str] | None = None) -> str:
    """从注册表自动生成给 LLM 的工具描述 prompt（JSON-in-text 模式用）。"""
    exclude = exclude or set()
    lines = [f"可用工具（文件操作默认在 {PROJECT_ROOT / 'workspace'} 下）："]
    for td in _REGISTRY.values():
        if td.name in exclude:
            continue
        if td.params:
            param_parts = []
            for k, v in td.params.items():
                param_parts.append(f"{k}" if not v else f"{k}")
            param_str = ", ".join(param_parts)
        else:
            param_str = ""
        lines.append(f"- {td.name}({param_str}) -> {td.description}")
    lines.append("")
    lines.append("只能返回 JSON：")
    lines.append('调用工具：{"tool": "工具名", "args": {...}, "thought": "简短原因"}')
    lines.append('最终回答：{"final": "给用户的回答"}')
    return "\n".join(lines)


def generate_tools_schema(exclude: set[str] | None = None) -> list[dict]:
    """生成 OpenAI function calling 格式的 tools schema。"""
    exclude = exclude or set()
    tools = []
    for td in _REGISTRY.values():
        if td.name in exclude:
            continue
        properties = {}
        for param_name, param_desc in td.params.items():
            properties[param_name] = {
                "type": "string",
                "description": param_desc or param_name,
            }
        schema = {
            "type": "function",
            "function": {
                "name": td.name,
                "description": td.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                },
            },
        }
        tools.append(schema)
    return tools


def get_registry() -> dict[str, ToolDef]:
    return _REGISTRY
