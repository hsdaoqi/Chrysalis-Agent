"""原始 LLM 对话日志记录。

每次 prompt/response 原文追加到 data/model_responses/model_responses_{PID}.txt。
"""

import os
from datetime import datetime
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "model_responses"


def write_llm_log(label: str, content: str) -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _LOG_DIR / f"model_responses_{os.getpid()}.txt"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"=== {label} === {ts}\n{content}\n\n")
