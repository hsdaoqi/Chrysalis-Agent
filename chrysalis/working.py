"""任务内短期工作记忆。

这一层只服务当前任务的多轮执行，不跨任务持久化。
稳定、可复用的信息以后再通过长期记忆流程写入 memory/。
"""

from dataclasses import dataclass, field


@dataclass
class WorkingMemory:
    key_info: str = ""
    related_sop: str = ""
    long_term_update_requested: str = ""
    max_key_info_chars: int = 1200
    max_related_sop_chars: int = 300
    max_reason_chars: int = 300
    _touched: bool = field(default=False, init=False, repr=False)

    def reset(self) -> None:
        self.key_info = ""
        self.related_sop = ""
        self.long_term_update_requested = ""
        self._touched = False

    def update_checkpoint(self, key_info: str = "", related_sop: str = "") -> dict:
        key_info = key_info.strip()
        related_sop = related_sop.strip()
        if key_info:
            self.key_info = key_info[: self.max_key_info_chars]
            self._touched = True
        if related_sop:
            self.related_sop = related_sop[: self.max_related_sop_chars]
            self._touched = True
        return {"ok": True, "message": "已更新短期工作记忆", "working_checkpoint": self.snapshot()}

    def request_long_term_update(self, reason: str = "") -> dict:
        reason = reason.strip() or "当前任务有可沉淀经验"
        self.long_term_update_requested = reason[: self.max_reason_chars]
        self._touched = True
        return {
            "ok": True,
            "message": "已标记长期记忆更新请求；请先读取 memory/memory_management_sop.md，再只写入经过工具验证的稳定信息。",
            "reason": self.long_term_update_requested,
        }

    def snapshot(self) -> dict:
        data = {}
        if self.key_info:
            data["key_info"] = self.key_info
        if self.related_sop:
            data["related_sop"] = self.related_sop
        if self.long_term_update_requested:
            data["long_term_update_requested"] = self.long_term_update_requested
        return data

    def to_prompt(self) -> str:
        snapshot = self.snapshot()
        if not snapshot:
            return ""
        lines = ["## 当前短期工作记忆"]
        for key, value in snapshot.items():
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    def append_to_prompt(self, prompt: str) -> str:
        working_prompt = self.to_prompt()
        if not working_prompt:
            return prompt
        return f"{prompt}\n\n{working_prompt}"
