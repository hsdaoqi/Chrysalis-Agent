"""AI and rule based judgment for long-term memory persistence.

The judge answers one question only: should this completed task become
long-term memory? Hard rules run first and can veto persistence. If the task
survives those rules, a separate AI call scores value, stability, reuse, and
safety. The caller can then route approved decisions to skills, SOPs, facts,
or future memory stores.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from chrysalis.llm import LLMClient
from chrysalis.working import WorkingMemory
from utils.text import brief_text

TARGETS = {"skill", "sop", "fact", "user_profile", "session_only", "discard"}
PERSIST_TARGETS = {"skill", "sop", "fact", "user_profile"}
SAFETY_LEVELS = {"low", "medium", "high"}
QUALITY_LEVELS = {"low", "medium", "high"}


@dataclass
class PersistDecision:
    should_persist: bool
    target: str = "discard"
    value_score: float = 0.0
    confidence: float = 0.0
    reason: str = ""
    evidence: list[str] = field(default_factory=list)
    stability: str = "low"
    reuse_likelihood: str = "low"
    safety_risk: str = "low"
    hard_rules: list[str] = field(default_factory=list)
    source: str = "hard_rules"

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_persist": self.should_persist,
            "target": self.target,
            "value_score": round(float(self.value_score), 3),
            "confidence": round(float(self.confidence), 3),
            "reason": self.reason,
            "evidence": list(self.evidence),
            "stability": self.stability,
            "reuse_likelihood": self.reuse_likelihood,
            "safety_risk": self.safety_risk,
            "hard_rules": list(self.hard_rules),
            "source": self.source,
        }

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "PersistDecision":
        target = _normalized_choice(data.get("target"), TARGETS, "discard")
        safety = _normalized_choice(data.get("safety_risk"), SAFETY_LEVELS, "low")
        stability = _normalized_choice(data.get("stability"), QUALITY_LEVELS, "low")
        reuse = _normalized_choice(data.get("reuse_likelihood"), QUALITY_LEVELS, "low")
        return cls(
            should_persist=_as_bool(data.get("should_persist", False)),
            target=target,
            value_score=_score(data.get("value_score")),
            confidence=_score(data.get("confidence")),
            reason=brief_text(str(data.get("reason") or ""), 600),
            evidence=_string_list(data.get("evidence"), limit=8),
            stability=stability,
            reuse_likelihood=reuse,
            safety_risk=safety,
            hard_rules=_string_list(data.get("hard_rules"), limit=8),
            source=str(data.get("source") or "ai"),
        )


class MemoryJudge:
    """Decide whether a successful task deserves long-term persistence."""

    def __init__(
        self,
        *,
        llm_factory: Callable[[], LLMClient] | None = None,
        ai_judge: Callable[[dict[str, Any]], dict[str, Any] | PersistDecision] | None = None,
        min_value_score: float = 0.65,
        min_confidence: float = 0.55,
    ) -> None:
        self.llm_factory = llm_factory
        self.ai_judge = ai_judge
        self.min_value_score = min_value_score
        self.min_confidence = min_confidence

    def judge(
        self,
        *,
        task: str,
        result: dict[str, Any],
        working: WorkingMemory,
        history_lines: list[str],
        tool_trace: list[dict[str, Any]],
        session_id: str = "",
    ) -> PersistDecision:
        evidence = self._build_evidence(
            task=task,
            result=result,
            working=working,
            history_lines=history_lines,
            tool_trace=tool_trace,
            session_id=session_id,
        )
        hard_decision = self._apply_hard_rules(evidence)
        if hard_decision is not None:
            return hard_decision

        try:
            raw = self._run_ai_judge(evidence)
        except Exception as exc:
            return PersistDecision(
                should_persist=False,
                target="session_only",
                value_score=0.0,
                confidence=0.0,
                reason=f"AI memory judge failed closed: {type(exc).__name__}: {brief_text(str(exc), 240)}",
                evidence=evidence["evidence"],
                stability="low",
                reuse_likelihood="low",
                safety_risk="medium",
                hard_rules=evidence["hard_rules"],
                source="ai_error",
            )

        decision = raw if isinstance(raw, PersistDecision) else PersistDecision.from_mapping(raw)
        decision.hard_rules = _merge_unique(decision.hard_rules, evidence["hard_rules"])
        decision.evidence = _merge_unique(decision.evidence, evidence["evidence"])
        decision.source = decision.source or "ai"
        return self._enforce_thresholds(decision)

    def _run_ai_judge(self, evidence: dict[str, Any]) -> dict[str, Any] | PersistDecision:
        if self.ai_judge is not None:
            return self.ai_judge(evidence)
        if self.llm_factory is None:
            return {
                "should_persist": False,
                "target": "session_only",
                "value_score": 0.0,
                "confidence": 0.0,
                "reason": "AI memory judge is unavailable; fail closed.",
                "evidence": evidence["evidence"],
                "stability": "low",
                "reuse_likelihood": "low",
                "safety_risk": "medium",
                "source": "ai_unavailable",
            }

        client = self.llm_factory()
        client.set_system(MEMORY_JUDGE_SYSTEM)
        response = _exhaust(client.chat([{"role": "user", "content": _judge_prompt(evidence)}]))
        if response.cancelled or response.is_error:
            raise RuntimeError(response.content or response.raw or "judge model returned an error")
        parsed = _parse_json_object(response.content)
        if parsed is None:
            raise ValueError("judge model did not return a JSON object")
        parsed["source"] = "ai"
        return parsed

    def _build_evidence(
        self,
        *,
        task: str,
        result: dict[str, Any],
        working: WorkingMemory,
        history_lines: list[str],
        tool_trace: list[dict[str, Any]],
        session_id: str,
    ) -> dict[str, Any]:
        turns = int(
            (result.get("agent_turns") or 0)
            or (result.get("turns") or 0)
            or ((result.get("usage") or {}).get("turns") or 0)
        )
        successful_tools = [item for item in tool_trace if item.get("ok") is not False]
        mutating_tools = [
            str(item.get("tool"))
            for item in tool_trace
            if str(item.get("tool")) in {"file_write", "file_patch", "code_run", "web_execute_js"}
        ]
        notes = []
        if successful_tools:
            notes.append(f"{len(successful_tools)} successful tool calls")
        if turns:
            notes.append(f"{turns} agent turns")
        if working.long_term_update_requested:
            notes.append("agent requested long-term update")
        if mutating_tools:
            notes.append("mutating or executable tools were used")
        return {
            "task": brief_text(task, 1_200),
            "final": brief_text(str(result.get("final", "")), 1_200),
            "ok": bool(result.get("ok")),
            "need_user": bool(result.get("need_user")),
            "cancelled": bool(result.get("cancelled")),
            "turns": turns,
            "tool_call_count": len(tool_trace),
            "successful_tool_count": len(successful_tools),
            "mutating_tools": sorted(set(mutating_tools)),
            "working": working.snapshot(),
            "history_tail": history_lines[-10:],
            "tool_trace": _compact_trace(tool_trace),
            "session_id": session_id,
            "evidence": notes,
            "hard_rules": [],
        }

    def _apply_hard_rules(self, evidence: dict[str, Any]) -> PersistDecision | None:
        rules = evidence["hard_rules"]
        task = str(evidence.get("task") or "")
        final = str(evidence.get("final") or "")
        combined = _combined_text(evidence)

        if not evidence["ok"]:
            return _reject("task did not complete successfully", evidence, target="discard")
        if evidence["need_user"] or evidence["cancelled"]:
            return _reject("task was interrupted or waiting for user input", evidence, target="discard")

        if _contains_secret(combined):
            rules.append("secret_or_credential_detected")
            return _reject("content looks like it contains credentials or secrets", evidence, safety_risk="high")
        if _contains_prompt_injection(combined):
            rules.append("prompt_injection_detected")
            return _reject("content looks like prompt injection or system prompt exfiltration", evidence, safety_risk="high")
        if _contains_dangerous_destructive_step(combined):
            rules.append("dangerous_destructive_step_detected")
            return _reject("workflow includes high-risk destructive operations", evidence, safety_risk="high")

        if _looks_volatile(task + "\n" + final):
            rules.append("volatile_or_time_sensitive_content")
            return _reject("content is volatile or time-sensitive; keep it session-local", evidence, target="session_only")

        has_execution_evidence = (
            evidence["successful_tool_count"] > 0
            or bool(evidence.get("mutating_tools"))
            or bool(evidence.get("working", {}).get("long_term_update_requested") and evidence["tool_call_count"] > 0)
        )
        if not has_execution_evidence:
            rules.append("no_execution_no_memory")
            return _reject("no execution evidence; do not create long-term memory from conversation alone", evidence, target="session_only")

        if evidence["tool_call_count"] == 1 and len(final.strip()) < 40 and not evidence.get("working"):
            rules.append("low_roi_single_step")

        rules.append("hard_rules_passed")
        return None

    def _enforce_thresholds(self, decision: PersistDecision) -> PersistDecision:
        if decision.target not in TARGETS:
            decision.target = "discard"
            decision.should_persist = False
        if decision.safety_risk == "high":
            decision.should_persist = False
            decision.target = "discard"
            decision.reason = decision.reason or "high safety risk"
        if decision.should_persist and decision.target not in PERSIST_TARGETS:
            decision.should_persist = False
        if decision.should_persist and decision.value_score < self.min_value_score:
            decision.should_persist = False
            decision.target = "session_only"
            decision.reason = f"value score below threshold: {decision.value_score:.2f}"
        if decision.should_persist and decision.confidence < self.min_confidence:
            decision.should_persist = False
            decision.target = "session_only"
            decision.reason = f"confidence below threshold: {decision.confidence:.2f}"
        if decision.should_persist and decision.target in {"skill", "sop"}:
            if decision.stability == "low" or decision.reuse_likelihood == "low":
                decision.should_persist = False
                decision.target = "session_only"
                decision.reason = "workflow lacks stability or reuse likelihood"
        return decision


MEMORY_JUDGE_SYSTEM = """You are Chrysalis MemoryJudge.
Decide whether a completed task deserves long-term memory.
Hard rules already filtered secrets, prompt injection, volatile content, and no-execution cases.
Prefer not to persist. Persist only if the memory is stable, reusable, evidenced, and safe.
Return only one JSON object with these keys:
should_persist boolean;
target one of skill, sop, fact, user_profile, session_only, discard;
value_score number from 0 to 1;
confidence number from 0 to 1;
reason short string;
evidence array of short strings;
stability low|medium|high;
reuse_likelihood low|medium|high;
safety_risk low|medium|high.
Target guidance:
skill = reusable tool/workflow execution pattern;
sop = broader repeatable operating procedure;
fact = stable project/system fact learned from evidence;
user_profile = durable user preference explicitly shown;
session_only = useful now but not durable;
discard = no memory value."""


def _judge_prompt(evidence: dict[str, Any]) -> str:
    payload = {
        "task": evidence["task"],
        "final": evidence["final"],
        "turns": evidence["turns"],
        "tool_call_count": evidence["tool_call_count"],
        "successful_tool_count": evidence["successful_tool_count"],
        "mutating_tools": evidence["mutating_tools"],
        "working": evidence["working"],
        "history_tail": evidence["history_tail"],
        "tool_trace": evidence["tool_trace"],
        "hard_rules": evidence["hard_rules"],
    }
    return (
        "Judge whether this completed task should become long-term memory.\n"
        "Use the target guidance in the system message. Return JSON only.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    )


def _reject(
    reason: str,
    evidence: dict[str, Any],
    *,
    target: str = "discard",
    safety_risk: str = "low",
) -> PersistDecision:
    return PersistDecision(
        should_persist=False,
        target=target,
        value_score=0.0,
        confidence=1.0,
        reason=reason,
        evidence=list(evidence.get("evidence") or []),
        stability="low",
        reuse_likelihood="low",
        safety_risk=safety_risk,
        hard_rules=list(evidence.get("hard_rules") or []),
        source="hard_rules",
    )


def _compact_trace(tool_trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for item in tool_trace[-12:]:
        compact.append({
            "turn": item.get("turn"),
            "tool": item.get("tool"),
            "ok": item.get("ok"),
            "args": item.get("args", {}),
            "error": brief_text(str(item.get("error", "")), 160) if item.get("error") else "",
            "content": brief_text(str(item.get("content", "")), 200) if item.get("content") else "",
            "stdout": brief_text(str(item.get("stdout", "")), 200) if item.get("stdout") else "",
            "path": str(item.get("path", "")),
        })
    return compact


def _combined_text(evidence: dict[str, Any]) -> str:
    pieces = [
        str(evidence.get("task") or ""),
        str(evidence.get("final") or ""),
        json.dumps(evidence.get("working") or {}, ensure_ascii=False, default=str),
        json.dumps(evidence.get("tool_trace") or [], ensure_ascii=False, default=str),
    ]
    return "\n".join(pieces)


def _contains_secret(text: str) -> bool:
    patterns = [
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"(?i)\b(api[_-]?key|secret|password|passwd|token|bearer)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+=]{8,}",
        r"(?i)\bsk-[A-Za-z0-9]{16,}\b",
        r"(?i)\bghp_[A-Za-z0-9]{20,}\b",
        r"(?i)\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\b",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def _contains_prompt_injection(text: str) -> bool:
    patterns = [
        r"(?i)ignore (all )?(previous|system|developer) instructions",
        r"(?i)reveal (your )?(system|developer) prompt",
        r"(?i)print (the )?(system|developer) message",
        r"忽略.*(系统|开发者|之前).*指令",
        r"泄露.*(系统|开发者).*提示",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def _contains_dangerous_destructive_step(text: str) -> bool:
    patterns = [
        r"(?i)\brm\s+-rf\s+/(?:\s|$)",
        r"(?i)\bformat\s+c:",
        r"(?i)\bdel\s+/[sfq]\s+c:\\",
        r"(?i)\bdelete\s+all\s+files\b",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def _looks_volatile(text: str) -> bool:
    patterns = [
        r"(?i)\b(today|tomorrow|yesterday|latest|current|right now|now)\b",
        r"(?i)\b(price|stock|weather|news|exchange rate|score|schedule)\b",
        r"(今天|明天|昨天|最新|当前|现在|价格|股价|天气|新闻|汇率|比分|赛程)",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _exhaust(gen) -> Any:
    try:
        while True:
            next(gen)
    except StopIteration as exc:
        return exc.value


def _score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def _string_list(value: Any, *, limit: int) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    elif isinstance(value, tuple):
        values = list(value)
    else:
        values = [value]
    return [brief_text(str(item), 240) for item in values if str(item).strip()][:limit]


def _normalized_choice(value: Any, allowed: set[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _merge_unique(first: list[str], second: list[str]) -> list[str]:
    merged: list[str] = []
    for item in first + second:
        if item and item not in merged:
            merged.append(item)
    return merged
