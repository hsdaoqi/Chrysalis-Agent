"""Chrysalis 工具模块。

通过 @tool 装饰器自动注册，导入即生效。
"""

import json

from chrysalis.tools.registry import run_tool, generate_tool_prompt, generate_tools_schema, get_registry

import chrysalis.tools.file_tools
import chrysalis.tools.web_tools
import chrysalis.tools.code_tools
import chrysalis.tools.agent_tools
import chrysalis.tools.vision_tools

TOOL_PROMPT = generate_tool_prompt()
TOOLS_SCHEMA = generate_tools_schema()


def dumps_observation(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)
