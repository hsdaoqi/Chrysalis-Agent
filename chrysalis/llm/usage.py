"""Token 用量跟踪：per-turn / per-task / session 级别累计，费用估算，持久化。"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from chrysalis.llm.types import Usage, _fmt_num

DEFAULT_PRICING = {
    "deepseek": {"input": 1.0, "output": 2.0, "cache_read": 0.1, "cache_write": 1.0},
    "gpt-4.1-mini": {"input": 0.4, "output": 1.6, "cache_read": 0.1, "cache_write": 0.4},
    "gpt-4.1": {"input": 2.0, "output": 8.0, "cache_read": 0.5, "cache_write": 2.0},
    "claude-sonnet": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-haiku": {"input": 0.8, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
}


@dataclass
class UsageRecord:
    timestamp: float
    task_brief: str
    usage: Usage
    turns: int
    elapsed_ms: int
    model: str
    cost: float

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "task_brief": self.task_brief,
            "usage": self.usage.to_dict(),
            "turns": self.turns,
            "elapsed_ms": self.elapsed_ms,
            "model": self.model,
            "cost": round(self.cost, 6),
        }

    @staticmethod
    def from_dict(d: dict) -> "UsageRecord":
        return UsageRecord(
            timestamp=d.get("timestamp", 0.0),
            task_brief=d.get("task_brief", ""),
            usage=Usage.from_dict(d.get("usage", {})),
            turns=d.get("turns", 0),
            elapsed_ms=d.get("elapsed_ms", 0),
            model=d.get("model", ""),
            cost=d.get("cost", 0.0),
        )


class UsageTracker:
    """跟踪 token 用量，支持 per-turn / per-task / session 三级累计。"""

    def __init__(
        self,
        persist_path: Path | None = None,
        pricing: dict[str, float] | None = None,
    ):
        self._persist_path = persist_path
        self._pricing = pricing  # {"input": x, "output": y, "cache_read": z, "cache_write": w}

        self.turn_usages: list[Usage] = []
        self.task_usage: Usage = Usage()
        self.session_usage: Usage = Usage()
        self.last_usage: Usage = Usage()

        self._task_records: list[UsageRecord] = []

    def begin_task(self) -> None:
        self.turn_usages = []
        self.task_usage = Usage()

    def record_turn(self, usage: Usage) -> None:
        self.last_usage = usage
        self.turn_usages.append(usage)
        self.task_usage += usage
        self.session_usage += usage

    def end_task(self, task_brief: str, elapsed_ms: int, model: str) -> None:
        cost = self.estimate_cost(self.task_usage, model)
        record = UsageRecord(
            timestamp=time.time(),
            task_brief=task_brief[:100],
            usage=self.task_usage,
            turns=len(self.turn_usages),
            elapsed_ms=elapsed_ms,
            model=model,
            cost=cost,
        )
        self._task_records.append(record)
        self._persist_record(record)

    def estimate_cost(self, usage: Usage, model: str = "") -> float:
        pricing = self._resolve_pricing(model)
        if not pricing:
            return 0.0
        cost = (
            usage.prompt_tokens * pricing.get("input", 0)
            + usage.completion_tokens * pricing.get("output", 0)
            + usage.cache_read_tokens * pricing.get("cache_read", 0)
            + usage.cache_creation_tokens * pricing.get("cache_write", 0)
        ) / 1_000_000
        return cost

    def task_cost(self, model: str = "") -> float:
        return self.estimate_cost(self.task_usage, model)

    def session_cost(self, model: str = "") -> float:
        return self.estimate_cost(self.session_usage, model)

    def format_task_summary(self, elapsed_ms: int = 0, model: str = "") -> str:
        if not self.task_usage:
            return ""
        cost = self.task_cost(model)
        parts = [self.task_usage.format()]
        if cost > 0:
            parts.append(f"~${cost:.4f}")
        parts.append(f"{len(self.turn_usages)} turns")
        if elapsed_ms:
            parts.append(_fmt_elapsed(elapsed_ms))
        return " | ".join(parts)

    def format_session_summary(self, model: str = "") -> str:
        if not self.session_usage:
            return ""
        cost = self.session_cost(model)
        parts = [
            f"session: {self.session_usage.format()}",
            f"{len(self._task_records)} tasks",
        ]
        if cost > 0:
            parts.append(f"~${cost:.4f}")
        return " | ".join(parts)

    def task_usage_dict(self) -> dict:
        d = self.task_usage.to_dict()
        d["turns"] = len(self.turn_usages)
        return d

    def _resolve_pricing(self, model: str) -> dict[str, float] | None:
        if self._pricing:
            return self._pricing
        model_lower = model.lower()
        for key, pricing in DEFAULT_PRICING.items():
            if key in model_lower:
                return pricing
        return None

    def _persist_record(self, record: UsageRecord) -> None:
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._persist_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            pass


def _fmt_elapsed(ms: int) -> str:
    if ms >= 60_000:
        return f"{ms / 60_000:.1f}min"
    if ms >= 1_000:
        return f"{ms / 1_000:.1f}s"
    return f"{ms}ms"
