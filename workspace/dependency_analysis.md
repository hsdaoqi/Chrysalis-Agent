# Chrysalis 依赖分析报告

## kernel.py 依赖列表

| 序号 | import 语句 | 模块 |
|------|------------|------|
| 1 | import argparse | argparse |
| 2 | import json | json |
| 3 | import sys | sys |
| 4 | import time | time |
| 5 | from chrysalis.agent_loop import AgentLoop | chrysalis.agent_loop |
| 6 | from chrysalis import subagent | chrysalis |
| 7 | from configs.config import AgentConfig | configs.config |
| 8 | from chrysalis.llm import LLMClient, create_client | chrysalis.llm |
| 9 | from utils.progress import ProgressCallback, stderr_progress | utils.progress |
| 10 | from chrysalis.session import SessionContext | chrysalis.session |

## agent_loop.py 依赖列表

| 序号 | import 语句 | 模块 |
|------|------------|------|
| 1 | import json | json |
| 2 | from pathlib import Path | pathlib |
| 3 | from typing import Callable | typing |
| 4 | from chrysalis.llm import LLMClient | chrysalis.llm |
| 5 | from chrysalis.observation import compact_observation | chrysalis.observation |
| 6 | from utils.get_prompts import get_system_prompt | utils.get_prompts |
| 7 | from utils.progress import ProgressCallback, summarize_action, summarize_observation | utils.progress |
| 8 | from chrysalis.tools import TOOL_PROMPT, dumps_observation, run_tool | chrysalis.tools |
| 9 | from chrysalis.working import WorkingMemory | chrysalis.working |

## 共同依赖

| 模块 | kernel.py | agent_loop.py |
|------|-----------|---------------|
| chrysalis.llm | ✅ | ✅ |
| json | ✅ | ✅ |
| utils.progress | ✅ | ✅ |

## 耦合程度总结

kernel.py 和 agent_loop.py 通过 chrysalis.llm 和 utils.progress 共享 LLM 核心能力和进度回调，且 kernel.py 直接 import AgentLoop 类产生单向强依赖，耦合程度中等偏高。
