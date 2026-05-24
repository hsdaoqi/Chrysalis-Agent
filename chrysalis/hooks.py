"""Lifecycle hook callbacks for Chrysalis."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

HookEvent = Literal["before_task", "after_task", "before_tool", "after_tool", "on_error"]
HookDecision = Literal["continue", "stop"]


@dataclass
class HookContext:
    event: HookEvent
    task: str = ""
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    observation: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    session_context: str = ""
    workspace: Path | None = None
    turn: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookOutcome:
    decision: HookDecision = "continue"
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def stop(self) -> bool:
        return self.decision == "stop"


HookCallback = Callable[[HookContext], HookOutcome | dict[str, Any] | None]


class HookManager:
    """Small in-process hook manager used by Kernel and AgentLoop."""

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[HookCallback]] = {
            "before_task": [],
            "after_task": [],
            "before_tool": [],
            "after_tool": [],
            "on_error": [],
        }

    def add(self, event: HookEvent, callback: HookCallback) -> None:
        self._hooks[event].append(callback)

    def emit(self, event: HookEvent, context: HookContext) -> HookOutcome:
        for callback in self._hooks.get(event, []):
            outcome = self._normalize(callback(context))
            if outcome.stop:
                return outcome
        return HookOutcome()

    def _normalize(self, value: HookOutcome | dict[str, Any] | None) -> HookOutcome:
        if value is None:
            return HookOutcome()
        if isinstance(value, HookOutcome):
            return value
        decision = "stop" if value.get("stop") else value.get("decision", "continue")
        if decision not in {"continue", "stop"}:
            decision = "continue"
        return HookOutcome(
            decision=decision,
            message=str(value.get("message", "")),
            data=dict(value.get("data", {})),
        )


class DisabledHookManager(HookManager):
    """Hook manager that never blocks runtime execution."""

    def add(self, event: HookEvent, callback: HookCallback) -> None:
        return None

    def emit(self, event: HookEvent, context: HookContext) -> HookOutcome:
        return HookOutcome()
