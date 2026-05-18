"""工具观察结果压缩。

Agent 可以拿到很大的文件、网页或 stdout，但下一轮模型并不需要完整原文。
这里保留结构、路径、状态和首尾内容，避免长程任务把上下文撑爆。
"""

from collections.abc import Mapping, Sequence

from utils.text import brief_text


TEXT_KEYS = {"content", "body", "stdout", "stderr", "error", "final", "message"}
MAX_TEXT_CHARS = 1200
MAX_LIST_ITEMS = 12


def compact_observation(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _compact_value(str(key), item) for key, item in value.items()}
    return _compact_value("", value)


def _compact_value(key: str, value: object) -> object:
    if isinstance(value, str):
        limit = MAX_TEXT_CHARS if key in TEXT_KEYS else 400
        return brief_text(value, limit)
    if isinstance(value, Mapping):
        return compact_observation(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)
        if len(items) <= MAX_LIST_ITEMS:
            return [compact_observation(item) for item in items]
        head_count = MAX_LIST_ITEMS // 2
        tail_count = MAX_LIST_ITEMS - head_count
        return (
            [compact_observation(item) for item in items[:head_count]]
            + [{"omitted_items": len(items) - MAX_LIST_ITEMS}]
            + [compact_observation(item) for item in items[-tail_count:]]
        )
    return value
