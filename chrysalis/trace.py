"""运行轨迹记录。

这里记录的是可复盘摘要，不保存完整上下文。
它用于失败分析、技能候选发现、记忆清理和反思报告。
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from chrysalis.text import brief_text


class TraceRecorder:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, task: str, result: dict) -> None:
        transcript = result.get("transcript", [])
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "task": brief_text(task, 600),
            "ok": bool(result.get("ok")),
            "final": brief_text(result.get("final", "")),
            "error": brief_text(result.get("error", "")),
            "failure_reason": _failure_reason(result, transcript),
            "elapsed_ms": int(result.get("elapsed_ms") or 0),
            "turns": _max_turn(transcript),
            "tool_calls": _count_key(transcript, "tool"),
            "skill_calls": _count_key(transcript, "skill"),
            "tools": _tool_events(transcript),
            "failed_tools": _failed_tool_events(transcript),
            "skill_names": _skill_names(transcript),
            "need_user": bool(result.get("need_user")),
            "user_question": brief_text(result.get("question", "")),
            "candidate_skill": bool(result.get("skill_candidate")),
            "wrote_skill": bool(result.get("skill")),
            "skill_path": str(result.get("skill", "")),
            "skill_steps": int(result.get("skill_steps") or 0),
            "skill_generator": str(result.get("skill_generator", "")),
            "skill_validation_error": brief_text(result.get("skill_validation_error", "")),
            "warnings": [brief_text(item, 300) for item in result.get("warnings", [])],
            "model_error": bool(result.get("model_error")),
            "exception_type": str(result.get("exception_type", "")),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _count_key(transcript: list[dict], key: str) -> int:
    return sum(1 for item in transcript if isinstance(item, dict) and item.get(key))


def _max_turn(transcript: list[dict]) -> int:
    turns = [int(item.get("turn", 0)) for item in transcript if isinstance(item, dict)]
    return max(turns, default=0)


def _tool_events(transcript: list[dict]) -> list[dict]:
    events = []
    for item in transcript:
        if not isinstance(item, dict) or not item.get("tool"):
            continue
        observation = item.get("observation") if isinstance(item.get("observation"), dict) else {}
        events.append({
            "turn": int(item.get("turn") or 0),
            "name": str(item.get("tool")),
            "ok": observation.get("ok"),
            "error": brief_text(observation.get("error", "")),
        })
    return events


def _failed_tool_events(transcript: list[dict]) -> list[dict]:
    return [event for event in _tool_events(transcript) if event.get("ok") is False]


def _skill_names(transcript: list[dict]) -> list[str]:
    names = []
    for item in transcript:
        if isinstance(item, dict) and item.get("skill"):
            names.append(str(item["skill"]))
    return names


def _failure_reason(result: dict, transcript: list[dict]) -> str:
    if result.get("ok"):
        return ""
    if result.get("need_user"):
        return "需要用户输入"
    if result.get("model_error"):
        return str(result.get("exception_type") or "模型/循环异常")
    if result.get("error"):
        return str(result.get("error"))
    failed_tools = _failed_tool_events(transcript)
    if failed_tools:
        latest = failed_tools[-1]
        return f"工具 {latest.get('name')} 失败：{latest.get('error')}"
    final = str(result.get("final", ""))
    return final
