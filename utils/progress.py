"""运行进度输出。"""

import sys
from typing import Callable

from utils.text import brief_text


ProgressCallback = Callable[[str], None]


def stderr_progress(message: str) -> None:
    sys.stderr.write(message.rstrip() + "\n")
    sys.stderr.flush()


def summarize_action(turn: int, action: dict | str | None) -> str:
    if not isinstance(action, dict):
        return f"第 {turn} 轮：模型返回无法解析的 JSON：{brief_text(action or '', 160)}"

    thought = str(action.get("thought", "")).strip()
    suffix = f" | {brief_text(thought, 120)}" if thought else ""

    if "final" in action:
        return f"第 {turn} 轮：最终回答 | {brief_text(action.get('final', ''), 220)}"
    if "skill" in action:
        return f"第 {turn} 轮：调用技能 {action.get('skill')}{suffix}"
    if "tool" in action:
        args = action.get("args") or {}
        path = args.get("path")
        target = f"({path})" if path else ""
        return f"第 {turn} 轮：调用工具 {action.get('tool')}{target}{suffix}"
    return f"第 {turn} 轮：未知动作 | {brief_text(action, 220)}"


def summarize_observation(turn: int, kind: str, observation: dict) -> str:
    ok = observation.get("ok")
    if "error" in observation:
        return f"第 {turn} 轮：{kind}结果 ok={ok} | {brief_text(observation.get('error'), 180)}"
    if "entries" in observation:
        return f"第 {turn} 轮：{kind}结果 ok={ok} | entries={len(observation.get('entries') or [])}"
    if "stdout" in observation:
        return f"第 {turn} 轮：{kind}结果 ok={ok} | {brief_text(observation.get('stdout'), 180)}"
    if "message" in observation:
        return f"第 {turn} 轮：{kind}结果 ok={ok} | {brief_text(observation.get('message'), 180)}"
    return f"第 {turn} 轮：{kind}结果 ok={ok}"
