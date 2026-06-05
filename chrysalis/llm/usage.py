"""Token 用量跟踪：per-turn / per-task / session 级别累计，费用估算，持久化。"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from chrysalis.llm.types import Usage, _fmt_num

DEFAULT_PRICING = {

}


@dataclass
class UsageRecord:
    """定义单次任务(Task)的 Token 用量与费用记录的数据结构。"""
    timestamp: float
    task_brief: str
    usage: Usage
    turns: int
    elapsed_ms: int
    model: str
    cost: float

    def to_dict(self) -> dict:
        """
        将当前记录对象转换为字典格式。
        作用：方便进行 JSON 序列化，以便存储到文件或通过 API 传输。
        """
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
        """
        从字典数据反序列化为 UsageRecord 对象。
        作用：用于从本地文件或数据库中读取历史数据并重建对象，使用了 .get() 提供安全的默认值防错。
        """
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
        """
        初始化 Token 用量追踪器。
        作用：配置本地存储路径、自定义价格表，并初始化各个级别（回合、任务、会话）的用量统计容器。
        """
        self._persist_path = persist_path
        self._pricing = pricing  # {"input": x, "output": y, "cache_read": z, "cache_write": w}

        self.turn_usages: list[Usage] = []
        self.task_usage: Usage = Usage()
        self.session_usage: Usage = Usage()
        self.last_usage: Usage = Usage()

        self._task_records: list[UsageRecord] = []

    def begin_task(self) -> None:
        """
        标记一个新任务(Task)的开始。
        作用：清空上一个任务的回合记录(turn_usages)和任务级总用量(task_usage)，为新任务的统计做准备。
        """
        self.turn_usages = []
        self.task_usage = Usage()

    def record_turn(self, usage: Usage) -> None:
        """
        记录单次交互（回合/Turn）的 Token 用量。
        作用：将传入的用量保存为最新用量，加入回合列表，并**同时累加**到当前任务(task)和全局会话(session)的统计中。
        """
        self.last_usage = usage
        self.turn_usages.append(usage)
        self.task_usage += usage
        self.session_usage += usage

    def end_task(self, task_brief: str, elapsed_ms: int, model: str) -> None:
        """
        结束当前任务(Task)并归档。
        作用：计算当前任务的预估费用，生成一条包含耗时、模型等详细信息的 UsageRecord 记录，将其保存到内存列表并触发本地持久化写入。
        """
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
        """
        计算指定 Token 用量的预估费用。
        作用：根据模型名称查找对应单价，按照 输入、输出及缓存的 Token 数量分别乘以单价并求和。（公式除以 1_000_000 是因为业界大模型通常按每百万 Token 计价）。
        """
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
        """
        获取当前任务(Task)的总预估费用。
        作用：专门针对当前任务积累的 `task_usage` 调用估算逻辑。
        """
        return self.estimate_cost(self.task_usage, model)

    def session_cost(self, model: str = "") -> float:
        """
        获取当前整个会话(Session)的总预估费用。
        作用：专门针对整个生命周期积累的 `session_usage` 调用估算逻辑。
        """
        return self.estimate_cost(self.session_usage, model)

    def format_task_summary(self, elapsed_ms: int = 0, model: str = "") -> str:
        """
        生成当前任务的摘要文本。
        作用：格式化输出直观的字符串（如："用量 | ~$0.0123 | 5 turns | 2.5s"），常用于控制台日志打印或终端 UI 展示。
        """
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
        """
        生成整个会话的摘要文本。
        作用：格式化输出当前会话的全局统计信息（如："session: 总用量 | 10 tasks | ~$0.1500"）。
        """
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
        """
        获取当前任务的用量字典。
        作用：将当前任务的用量转换为字典，并额外注入 `turns`（回合数）字段，方便供其他外部业务逻辑调用。
        """
        d = self.task_usage.to_dict()
        d["turns"] = len(self.turn_usages)
        return d

    def _resolve_pricing(self, model: str) -> dict[str, float] | None:
        """
        解析并获取对应模型的价格表。
        作用：优先使用初始化时传入的自定义价格，如果没有，则在 `DEFAULT_PRICING` 字典中对模型名称进行不区分大小写的模糊匹配并返回价格。
        """
        if self._pricing:
            return self._pricing
        model_lower = model.lower()
        for key, pricing in DEFAULT_PRICING.items():
            if key in model_lower:
                return pricing
        return None

    def _persist_record(self, record: UsageRecord) -> None:
        """
        将单条记录写入本地文件。
        作用：采用 JSON Lines (JSONL) 格式，将记录**追加 (mode="a")** 到日志文件中。自动创建缺失的文件夹，并静默处理(pass)任何写入错误，防止因日志写入失败导致主业务崩溃。
        """
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._persist_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            pass


def _fmt_elapsed(ms: int) -> str:
    """
    格式化毫秒数为人类可读的时间字符串。
    作用：根据数值大小，将毫秒自动转换为分钟(min)、秒(s)或保留毫秒(ms)格式。
    """
    if ms >= 60_000:
        return f"{ms / 60_000:.1f}min"
    if ms >= 1_000:
        return f"{ms / 1_000:.1f}s"
    return f"{ms}ms"
