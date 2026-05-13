"""交互会话内的短期上下文。

它和 WorkingMemory 不同：
- WorkingMemory 是单个任务内部的多轮执行状态，每次 run() 开始清空。
- SessionContext 是交互模式下多个用户任务之间的最近上下文，不写入长期记忆。
"""

from dataclasses import dataclass, field

from chrysalis.text import brief_text


@dataclass
class SessionTurn:
    task: str
    final: str

#TODO 修改窗口大小，并在达到窗口大小时自动压缩
@dataclass
class SessionContext:
    max_turns: int = 6
    max_text_chars: int = 500
    turns: list[SessionTurn] = field(default_factory=list)

    def remember(self, task: str, result: dict) -> None:
        final = result.get("final") or result.get("error") or ""
        self.turns.append(SessionTurn(
            task=brief_text(str(task), self.max_text_chars),
            final=brief_text(str(final), self.max_text_chars),
        ))
        self.turns = self.turns[-self.max_turns:]

    def context(self) -> str:
        if not self.turns:
            return ""
        lines = ["## 本次交互会话的最近上下文"]
        for index, turn in enumerate(self.turns, 1):
            lines.append(f"{index}. 用户任务：{turn.task}")
            lines.append(f"   上次结果：{turn.final}")
        return "\n".join(lines)

    def clear(self) -> None:
        self.turns.clear()
