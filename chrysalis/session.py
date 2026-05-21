"""Lightweight in-process session context.

This is separate from SessionStore, which persists canonical LLM history.  The
class here keeps a compact human-readable trail for callers that need a small
continuation block across tasks.
"""

from __future__ import annotations


class SessionContext:
    def __init__(self, max_turns: int = 6) -> None:
        self.max_turns = max(1, max_turns)
        self._turns: list[tuple[str, dict]] = []

    def remember(self, task: str, result: dict) -> None:
        self._turns.append((task, result))
        if len(self._turns) > self.max_turns:
            del self._turns[: len(self._turns) - self.max_turns]

    def context(self) -> str:
        if not self._turns:
            return ""
        lines = ["## Recent Session Context"]
        for task, result in self._turns:
            lines.append(f"[USER] {task}")
            final = result.get("final")
            if final is None:
                final = result.get("error", result)
            lines.append(f"[Agent] {final}")
        return "\n".join(lines)

    def clear(self) -> None:
        self._turns.clear()
