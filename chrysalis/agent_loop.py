"""GenericAgent 的核心行动循环。"""

from __future__ import annotations

import inspect
import json
import threading
from dataclasses import replace
from pathlib import Path
from typing import Callable

from chrysalis.context_engine import ContextEngine
from chrysalis.hooks import DisabledHookManager, HookContext, HookManager
from chrysalis.llm import LLMClient
from chrysalis.observation import compact_observation
from chrysalis.permission import FullAccessPermissionEngine, PermissionEngine
from chrysalis.memory import MemoryJudge, PersistDecision
from chrysalis.skills.curator import SkillCurator
from chrysalis.tools import TOOL_PROMPT, TOOLS_SCHEMA, dumps_observation, run_tool
from chrysalis.working import WorkingMemory
from utils.get_prompts import get_system_prompt
from utils.progress import ProgressCallback, summarize_action, summarize_observation
from utils.text import brief_text

MAX_SUMMARY_LEN = 80


class AgentLoop:
    def __init__(
        self,
        llm: LLMClient,
        workspace: Path,
        max_turns: int = 12,
        progress: ProgressCallback | None = None,
        history: list[str] | None = None,
        on_stream_chunk: "Callable[[str], None] | None" = None,
        on_tool_call: "Callable[[str, dict, dict | None], None] | None" = None,
        on_tool_stream: "Callable[[str, dict, str], None] | None" = None,
        on_permission_request: "Callable[[dict], str] | None" = None,
        on_thinking: "Callable[[str], None] | None" = None,
        on_working_change: "Callable[[dict], None] | None" = None,
        on_trace_event: "Callable[[dict], None] | None" = None,
        use_function_calling: bool = True,
        tools_schema: list[dict] | None = None,
        tool_prompt: str | None = None,
        context_engine: ContextEngine | None = None,
        permission_engine: PermissionEngine | None = None,
        hooks: HookManager | None = None,
        memory_judge: MemoryJudge | None = None,
        system_prompt_preamble: str | None = None,
    ):
        self.llm = llm
        self.workspace = workspace
        self.max_turns = max_turns
        self.progress = progress
        self.on_stream_chunk = on_stream_chunk
        self.on_tool_call = on_tool_call
        self.on_tool_stream = on_tool_stream
        self.on_permission_request = on_permission_request
        self.on_thinking = on_thinking
        self.on_working_change = on_working_change
        self.on_trace_event = on_trace_event
        self.use_function_calling = use_function_calling
        self.tools_schema = tools_schema
        self.tool_prompt = tool_prompt
        self.working = WorkingMemory()
        self.history_info: list[str] = history if history is not None else []
        self.context_engine = context_engine or ContextEngine()
        self.permission_engine = permission_engine or FullAccessPermissionEngine()
        self.system_prompt_preamble = str(system_prompt_preamble or "").strip()
        self.hooks = hooks or DisabledHookManager()
        self.skill_curator = SkillCurator(min_turns=4, min_tool_calls=2, auto_promote=False)
        self.memory_judge = memory_judge or MemoryJudge(llm_factory=self._build_memory_judge_client)
        self._tool_trace: list[dict] = []
        self._cancel_event = threading.Event()
        self._guidance_lock = threading.Lock()
        self._pending_guidance: list[str] = []
        self._current_turn = 0
        self._resume_state: dict | None = None

    def run(
        self,
        task: str,
        session_context: str = "",
        images: list[dict] | None = None,
        ui_kind: str = "",
        resume: dict | None = None,
    ) -> dict:
        self._cancel_event.clear()
        self._current_turn = 0
        self._resume_state = None
        resume_context = ""
        if resume:
            resume_context = self._apply_resume_state(resume)
        else:
            self.working.reset()
            self._tool_trace = []
        self.history_info.append(f"[USER]: {brief_text(task, 400)}")
        if resume_context:
            session_context = "\n\n".join(part for part in (session_context, resume_context) if part)

        before_task = self.hooks.emit("before_task", HookContext(
            event="before_task",
            task=task,
            session_context=session_context,
            workspace=self.workspace,
        ))
        if before_task.stop:
            return {"ok": False, "blocked": True, "final": before_task.message or "task stopped by hook"}

        permission = self.permission_engine.assess_task(task, session_context=session_context)
        if permission.denied:
            self._emit_trace("permission_denied", scope="task", reason=permission.reason, risk=permission.risk)
            return {"ok": False, "blocked": True, "final": permission.reason, "permission": permission.to_result()}
        if permission.needs_user:
            result = permission.to_result()
            self._emit_trace("permission_requested", scope="task", request=result)
            return result

        system_prompt = get_system_prompt(include_memory=False)
        if self.system_prompt_preamble:
            system_prompt = self.system_prompt_preamble + "\n\n" + system_prompt
        assembled = self.context_engine.assemble(
            base_system=system_prompt,
            task=task,
            working=self.working,
            history_lines=self.history_info,
            session_context=session_context,
            include_history_anchor=False,
            inline_runtime_context=False,
        )
        if hasattr(self.llm, "set_context_budget"):
            self.llm.set_context_budget(assembled.budget)
        self._emit_trace(
            "context_assembled",
            included=assembled.included,
            budget=assembled.budget,
            system_chars=len(assembled.system),
            runtime_context_chars=len(assembled.runtime_context),
            history_lines=len(self.history_info),
            task_chars=len(task),
            session_context_chars=len(session_context or ""),
        )

        if self.use_function_calling:
            result = self._run_function_calling(
                _with_runtime_context(task, assembled.runtime_context),
                assembled.system,
                session_context,
                images=images,
                task_for_tools=task,
                ui_kind=ui_kind,
            )
        else:
            result = self._run_json_in_text(
                _with_runtime_context(task, assembled.runtime_context),
                assembled.system,
                session_context,
                images=images,
                task_for_tools=task,
                ui_kind=ui_kind,
            )

        after_task = self.hooks.emit("after_task", HookContext(
            event="after_task",
            task=task,
            session_context=session_context,
            workspace=self.workspace,
            result=result,
        ))
        if after_task.stop:
            return {"ok": False, "blocked": True, "final": after_task.message or "task stopped by hook"}
        if result.get("ok"):
            memory_decision = self._judge_memory(task, result, session_context)
            result["memory_decision"] = memory_decision.to_dict()
            artifact = self._maybe_create_skill_draft(task, result, session_context, memory_decision)
            if artifact.get("ok"):
                result["skill_artifact"] = artifact.get("skill")
                result["skill_draft"] = artifact.get("skill")
        return result

    def cancel(self) -> None:
        self._cancel_event.set()
        if hasattr(self.llm, "cancel"):
            self.llm.cancel()

    def guide(self, text: str) -> bool:
        guidance = str(text or "").strip()
        if not guidance:
            return False
        with self._guidance_lock:
            self._pending_guidance.append(guidance)
        self._emit_trace("guidance_queued", content_preview=brief_text(guidance, 240))
        return True

    def _cancelled_result(self) -> dict:
        return {
            "ok": False,
            "cancelled": True,
            "final": "任务已中断",
            "checkpoint": self._build_checkpoint(),
        }

    def _build_checkpoint(self) -> dict:
        """构造中断时的续跑状态。不碰文件系统——落盘交给上层（Kernel/SessionStore）。"""
        return {
            "working": self.working.to_dict(),
            "tool_trace": list(self._tool_trace),
            "history_info": list(self.history_info),
            "turn": self._current_turn,
        }

    def _apply_resume_state(self, resume: dict) -> str:
        """从 checkpoint 还原中断状态，返回注入给模型的「已完成步骤/事实」摘要。"""
        self._resume_state = resume
        working = resume.get("working")
        if isinstance(working, dict):
            self.working.restore(working)
        tool_trace = resume.get("tool_trace")
        self._tool_trace = list(tool_trace) if isinstance(tool_trace, list) else []
        history_info = resume.get("history_info")
        if isinstance(history_info, list):
            # history_info 是 self 与 Kernel 共享的同一个 list，就地替换内容而非换引用
            self.history_info[:] = [str(line) for line in history_info]
        return self._resume_summary(resume)

    def _resume_summary(self, resume: dict) -> str:
        """把已完成的工具调用 / 工作记忆压成一段提示，让模型从断点续跑而非重来。

        最关键的是最后一次工具调用：mid-turn 取消会丢掉它的 tool_result（没存进
        canonical history），如果不告诉模型它已经跑过，模型会重复执行有副作用的工具。
        """
        lines = [
            "[任务续跑] 这是一个**被中断后继续**的任务，不是新任务。下面是中断前已经完成的工作，",
            "请从断点继续，不要重新从头开始，尤其不要重复执行已经成功的、有副作用的操作（写文件、跑命令、发消息等）。",
        ]
        turn = resume.get("turn")
        if isinstance(turn, int) and turn > 0:
            lines.append(f"- 中断时已执行到第 {turn} 轮。")

        trace = resume.get("tool_trace") or []
        if isinstance(trace, list) and trace:
            lines.append("- 已执行的工具调用（按顺序）：")
            for entry in trace[-12:]:
                if not isinstance(entry, dict):
                    continue
                tool = str(entry.get("tool") or "")
                ok = entry.get("ok")
                status = "成功" if ok else ("失败" if ok is False else "?")
                detail = ""
                if entry.get("path"):
                    detail = f" -> {entry.get('path')}"
                elif entry.get("error"):
                    detail = f" 错误: {brief_text(str(entry.get('error')), 120)}"
                elif entry.get("content"):
                    detail = f" -> {brief_text(str(entry.get('content')), 120)}"
                args = entry.get("args") or {}
                args_str = brief_text(json.dumps(args, ensure_ascii=False, default=str), 120) if args else ""
                lines.append(f"  - [{status}] {tool} {args_str}{detail}".rstrip())

        working_prompt = self.working.to_prompt()
        if working_prompt:
            lines.append(working_prompt)
        return "\n".join(lines)

    def _run_function_calling(
        self,
        task: str,
        system: str,
        session_context: str = "",
        images: list[dict] | None = None,
        task_for_tools: str | None = None,
        ui_kind: str = "",
    ) -> dict:
        tool_task = task_for_tools or task
        tools = self.tools_schema or TOOLS_SCHEMA
        self.llm.set_system(system)
        self.llm.set_tools(tools)

        messages = [{
            "role": "user",
            "content": task,
            "images": list(images or []),
            "meta": _display_meta(task_for_tools or task, ui_kind),
        }]
        for turn in range(1, self.max_turns + 1):
            self._current_turn = turn
            if self._cancel_event.is_set():
                return self._cancelled_result()

            response = _exhaust_generator(
                _chat_with_optional_turn(
                    self.llm,
                    messages,
                    tools=tools,
                    cancel_event=self._cancel_event,
                    turn=turn,
                ),
                self.on_stream_chunk,
            )
            if response.cancelled or self._cancel_event.is_set():
                return self._cancelled_result()

            if response.thinking and self.on_thinking:
                self.on_thinking(response.thinking)

            if response.tool_calls:
                tc = response.tool_calls[0]
                tool_name = tc.name
                try:
                    args = json.loads(tc.arguments) if tc.arguments else {}
                except json.JSONDecodeError:
                    args = {}

                self._progress(summarize_action(turn, {"tool": tool_name, "args": args}))
                self.history_info.append(f"[Agent] 调用工具 {tool_name}")
                self._emit_trace("tool_started", turn=turn, tool=tool_name, args=self._trace_args(args))

                if self.on_tool_call:
                    self.on_tool_call(tool_name, args, None)
                if self._cancel_event.is_set():
                    return self._cancelled_result()

                observation = self._execute_tool_with_guards(
                    task=tool_task,
                    tool_name=tool_name,
                    args=args,
                    turn=turn,
                    session_context=session_context,
                )
                self._record_tool_trace(turn, tool_name, args, observation)
                self._emit_trace(
                    "tool_completed",
                    turn=turn,
                    tool=tool_name,
                    args=self._trace_args(args),
                    observation=self._trace_observation(observation),
                    ok=bool(isinstance(observation, dict) and observation.get("ok")),
                )
                if self.on_tool_call:
                    self.on_tool_call(tool_name, args, observation)

                self._handle_agent_tool_side_effects(observation)
                compact = compact_observation(observation)
                self._progress(summarize_observation(turn, "工具", compact))

                if isinstance(observation, dict) and observation.get("need_user"):
                    result = {
                        "ok": False,
                        "need_user": True,
                        "final": str(observation.get("question", "需要用户输入。")),
                        "question": observation.get("question", ""),
                        "candidates": observation.get("candidates", []),
                        "reason": observation.get("reason", "need_user"),
                    }
                    for key in ("permission_request", "options", "grant_key", "tool", "risk", "details", "decision"):
                        if key in observation:
                            result[key] = observation[key]
                    return result

                obs_text = dumps_observation(compact)
                images = []
                if isinstance(observation, dict) and observation.get("_image"):
                    images.append(observation["_image"])
                next_content = self._next_prompt_with_anchor(obs_text)
                messages = [{
                    "role": "user",
                    "content": next_content,
                    "images": images,
                    "tool_results": [{
                        "tool_use_id": tc.id,
                        "content": obs_text,
                    }],
                }]
                continue

            content = response.content.strip()
            if content:
                self._progress(summarize_action(turn, {"final": content}))
                self._append_history_from_action({"final": content}, raw=content)
                return {"ok": True, "final": content, "agent_turns": turn}

            messages = [{"role": "user", "content": self._next_prompt_with_anchor("请继续执行任务或给出最终回答。")}]

        return {"ok": False, "final": "达到最大轮数，仍未得到最终回答。"}

    def _run_json_in_text(
        self,
        task: str,
        system_prompt: str,
        session_context: str = "",
        images: list[dict] | None = None,
        task_for_tools: str | None = None,
        ui_kind: str = "",
    ) -> dict:
        tool_task = task_for_tools or task
        tool_prompt = self.tool_prompt or TOOL_PROMPT
        messages = [
            {"role": "system", "content": system_prompt + "\n\n" + tool_prompt},
            {
                "role": "user",
                "content": f"任务：\n{task}",
                "images": list(images or []),
                "meta": _display_meta(f"任务：\n{tool_task}", ui_kind),
            },
        ]

        for turn in range(1, self.max_turns + 1):
            self._current_turn = turn
            if self._cancel_event.is_set():
                return self._cancelled_result()

            response = _exhaust_generator(
                _chat_with_optional_turn(
                    self.llm,
                    messages,
                    cancel_event=self._cancel_event,
                    turn=turn,
                ),
                self.on_stream_chunk,
            )
            if response.cancelled or self._cancel_event.is_set():
                return self._cancelled_result()

            raw = response.content.strip()
            action = _parse_json(raw)
            self._progress(summarize_action(turn, action or raw))

            if not action:
                self._append_history_from_action(action, raw)
                messages.append({"role": "user", "content": "JSON 无效。请返回工具调用 JSON，或返回 final JSON。"})
                continue

            if "final" in action:
                final = str(action["final"])
                self._append_history_from_action(action, raw)
                return {"ok": True, "final": final, "agent_turns": turn}

            tool = action.get("tool")
            args = action.get("args") or {}
            self._append_history_from_action(action, raw)
            self._emit_trace("tool_started", turn=turn, tool=str(tool), args=self._trace_args(args))
            if self.on_tool_call:
                self.on_tool_call(str(tool), args, None)
            if self._cancel_event.is_set():
                return self._cancelled_result()

            tool_name = str(tool)
            observation = self._execute_tool_with_guards(
                task=tool_task,
                tool_name=tool_name,
                args=args,
                turn=turn,
                session_context=session_context,
            )
            self._record_tool_trace(turn, tool_name, args, observation)
            self._emit_trace(
                "tool_completed",
                turn=turn,
                tool=tool_name,
                args=self._trace_args(args),
                observation=self._trace_observation(observation),
                ok=bool(isinstance(observation, dict) and observation.get("ok")),
            )
            if self.on_tool_call:
                self.on_tool_call(tool_name, args, observation)
            self._handle_agent_tool_side_effects(observation)
            compact = compact_observation(observation)
            self._progress(summarize_observation(turn, "工具", compact))
            if isinstance(observation, dict) and observation.get("need_user"):
                result = {
                    "ok": False,
                    "need_user": True,
                    "final": str(observation.get("question", "需要用户输入。")),
                    "question": observation.get("question", ""),
                    "candidates": observation.get("candidates", []),
                    "reason": observation.get("reason", "need_user"),
                }
                for key in ("permission_request", "options", "grant_key", "tool", "risk", "details", "decision"):
                    if key in observation:
                        result[key] = observation[key]
                return result
            obs_text = "观察结果：\n" + dumps_observation(compact)
            messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
            messages.append({"role": "user", "content": self._next_prompt_with_anchor(obs_text)})

        return {"ok": False, "final": "达到最大轮数，仍未得到最终回答。"}

    def _append_history_from_action(self, action: dict | None, raw: str, *, prefix: str = "[Agent]") -> None:
        if not action:
            summary = "JSON解析失败"
        elif "final" in action:
            summary = brief_text(str(action["final"]), MAX_SUMMARY_LEN)
        elif "tool" in action:
            tool = action.get("tool", "")
            args = {k: v for k, v in (action.get("args") or {}).items() if not str(k).startswith("_")}
            args_str = brief_text(json.dumps(args, ensure_ascii=False), 50)
            summary = f"调用工具 {tool}, args: {args_str}"
        else:
            summary = "直接回答"
        self.history_info.append(f"{prefix} {summary}")

    def _get_anchor_prompt(self) -> str:
        prompt = "\n" + self.context_engine.session_anchor(
            self.history_info,
            self.working,
            max_chars=3_000,
        )
        if self.working.related_sop:
            prompt += f"\n有不清楚的地方请再次读取 {self.working.related_sop}"
        return prompt

    def _next_prompt_with_anchor(self, prompt: str) -> str:
        return prompt + self._pop_guidance_prompt() + self._get_anchor_prompt()

    def _pop_guidance_prompt(self) -> str:
        with self._guidance_lock:
            items = [item for item in self._pending_guidance if item.strip()]
            self._pending_guidance.clear()
        if not items:
            return ""
        joined = "\n".join(f"- {item}" for item in items)
        summary = " | ".join(items)
        self.history_info.append(f"[USER guidance] {brief_text(summary, 400)}")
        self._emit_trace("guidance_applied", count=len(items), content_preview=brief_text(summary, 240))
        return (
            "\n\n[USER GUIDANCE WHILE TASK IS RUNNING]\n"
            "The user added these instructions after the task started. Treat them as the latest direction for the current task. "
            "Adjust the plan before continuing, and avoid work that conflicts with them.\n"
            f"{joined}\n"
            "[/USER GUIDANCE]\n"
        )

    def _handle_agent_tool_side_effects(self, observation: dict) -> None:
        if not isinstance(observation, dict):
            return
        changed_working = False
        if observation.get("_todo"):
            self.working.update_todos(
                observation.get("todos") or [],
                goal=str(observation.get("goal", "")),
                action=str(observation.get("todo_action", "set")),
            )
            changed_working = True
        if observation.get("_plan"):
            self.working.update_plan(
                goal=str(observation.get("goal", "")),
                steps=observation.get("steps"),
                acceptance_criteria=observation.get("acceptance_criteria"),
                evidence=observation.get("evidence"),
                status=str(observation.get("status", "")),
                summary=str(observation.get("summary", "")),
                blocker=str(observation.get("blocker", "")),
                action=str(observation.get("plan_action", "set")),
            )
            changed_working = True
        self.working.tick_round()
        if observation.get("_checkpoint"):
            self.working.update_checkpoint(
                key_info=str(observation.get("key_info", "")),
                related_sop=str(observation.get("related_sop", "")),
            )
            changed_working = True
        if observation.get("_long_term"):
            self.working.request_long_term_update(
                reason=str(observation.get("reason", "")),
            )
            changed_working = True
        if changed_working and self.on_working_change:
            self.on_working_change(self.working.state_snapshot())
        if changed_working:
            self._emit_trace("working_updated", snapshot=self.working.state_snapshot())

    def _record_tool_trace(self, turn: int, tool_name: str, args: dict, observation: dict | None) -> None:
        entry = {
            "turn": turn,
            "tool": tool_name,
            "args": {k: v for k, v in args.items() if not str(k).startswith("_")},
        }
        if isinstance(observation, dict):
            entry["ok"] = observation.get("ok", False)
            if observation.get("error"):
                entry["error"] = brief_text(str(observation.get("error")), 240)
            if observation.get("content"):
                entry["content"] = brief_text(str(observation.get("content")), 240)
            if observation.get("stdout"):
                entry["stdout"] = brief_text(str(observation.get("stdout")), 240)
            if observation.get("path"):
                entry["path"] = str(observation.get("path"))
        self._tool_trace.append(entry)

    def _maybe_create_skill_draft(
        self,
        task: str,
        result: dict,
        session_context: str = "",
        memory_decision: PersistDecision | None = None,
    ) -> dict:
        try:
            return self.skill_curator.maybe_create_draft(
                task=task,
                result=result,
                working=self.working,
                history_lines=self.history_info,
                tool_trace=self._tool_trace,
                session_id="",
                memory_decision=memory_decision,
            )
        except Exception as exc:
            return {"ok": False, "error": f"skill draft failed: {type(exc).__name__}: {exc}"}

    def _judge_memory(self, task: str, result: dict, session_context: str = "") -> PersistDecision:
        try:
            return self.memory_judge.judge(
                task=task,
                result=result,
                working=self.working,
                history_lines=self.history_info,
                tool_trace=self._tool_trace,
                session_id=session_context,
            )
        except Exception as exc:
            return PersistDecision(
                should_persist=False,
                target="session_only",
                value_score=0.0,
                confidence=0.0,
                reason=f"memory judge failed: {type(exc).__name__}: {exc}",
                evidence=[],
                stability="low",
                reuse_likelihood="low",
                safety_risk="medium",
                source="judge_error",
            )

    def _build_memory_judge_client(self) -> LLMClient:
        session = self.llm.session
        if hasattr(session, "sessions") and getattr(session, "sessions"):
            configs = [_tune_judge_config(s.config) for s in session.sessions]
        else:
            configs = _tune_judge_config(session.config)
        from chrysalis.llm import create_client
        return create_client(configs, tracker=self.llm.tracker)

    def _execute_tool_with_guards(
        self,
        task: str,
        tool_name: str,
        args: dict,
        turn: int,
        session_context: str = "",
    ) -> dict:
        permission = self.permission_engine.assess_tool(
            tool_name,
            args,
            workspace=self.workspace,
            session_context=session_context,
        )
        if permission.needs_user:
            request = permission.to_result()
            self._emit_trace("permission_requested", scope="tool", turn=turn, tool=tool_name, request=request)
            resolved = self._resolve_permission_request(request)
            self._emit_trace(
                "permission_resolved",
                scope="tool",
                turn=turn,
                tool=tool_name,
                action=str(resolved.get("action", "")),
            )
            action = str(resolved.get("action", ""))
            if action == "allow":
                permission = self.permission_engine.assess_tool(
                    tool_name,
                    args,
                    workspace=self.workspace,
                    session_context=str(resolved.get("context", "")),
                )
            elif action == "deny":
                self._emit_trace("permission_denied", scope="tool", turn=turn, tool=tool_name, reason="user denied")
                return {"ok": False, "blocked": True, "error": "用户拒绝了权限请求"}
            elif action == "detail":
                return {
                    "ok": False,
                    "need_user": True,
                    "question": str(resolved.get("context", "")),
                    "candidates": [],
                    "reason": "permission_detail",
                }
            else:
                return permission.to_result()
        if permission.denied:
            self._emit_trace("permission_denied", scope="tool", turn=turn, tool=tool_name, reason=permission.reason, risk=permission.risk)
            return {
                "ok": False,
                "blocked": True,
                "error": permission.reason,
                "permission": permission.to_result(),
            }

        before_tool = self.hooks.emit("before_tool", HookContext(
            event="before_tool",
            task=task,
            tool=tool_name,
            args=args,
            session_context=session_context,
            workspace=self.workspace,
            turn=turn,
        ))
        if before_tool.stop:
            return {"ok": False, "blocked": True, "error": before_tool.message or "tool stopped by hook"}

        try:
            on_stream = None
            if self.on_tool_stream is not None:
                on_stream = lambda chunk: self.on_tool_stream(tool_name, args, chunk)
            observation = run_tool(tool_name, args, self.workspace, on_stream=on_stream)
        except Exception as exc:
            observation = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self.hooks.emit("on_error", HookContext(
                event="on_error",
                task=task,
                tool=tool_name,
                args=args,
                observation=observation,
                session_context=session_context,
                workspace=self.workspace,
                turn=turn,
            ))

        self.hooks.emit("after_tool", HookContext(
            event="after_tool",
            task=task,
            tool=tool_name,
            args=args,
            observation=observation,
            session_context=session_context,
            workspace=self.workspace,
            turn=turn,
        ))
        return observation

    def _resolve_permission_request(self, request: dict) -> dict:
        if self.on_permission_request is None:
            return {"action": "ask", "context": ""}
        choice = self.on_permission_request(request)
        return self.permission_engine.resolve_user_choice(request, choice)

    def _progress(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message)

    def _emit_trace(self, kind: str, **payload) -> None:
        if self.on_trace_event is None:
            return
        try:
            self.on_trace_event({"kind": kind, **payload})
        except Exception:
            pass

    def _trace_args(self, args: dict) -> dict:
        safe: dict = {}
        for key, value in (args or {}).items():
            if str(key).startswith("_"):
                continue
            safe[str(key)] = _trace_preview(value)
        return safe

    def _trace_observation(self, observation: dict | None) -> dict:
        if not isinstance(observation, dict):
            return {}
        preview: dict = {
            "ok": bool(observation.get("ok")),
        }
        for key in ("error", "message", "content", "stdout", "stderr", "path", "question", "reason"):
            if key in observation and observation.get(key) not in (None, ""):
                preview[key] = _trace_preview(observation.get(key), 600)
        if observation.get("need_user"):
            preview["need_user"] = True
        if observation.get("_todo"):
            preview["working_kind"] = "todo"
        if observation.get("_plan"):
            preview["working_kind"] = "plan"
        if observation.get("_checkpoint"):
            preview["working_kind"] = "checkpoint"
        return preview


def _parse_json(text: str) -> dict | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _trace_preview(value, limit: int = 240):
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            text = str(value)
    return brief_text(text, limit)


def _with_runtime_context(task: str, runtime_context: str) -> str:
    runtime = (runtime_context or "").strip()
    if not runtime:
        return task
    return task.rstrip() + "\n\n" + runtime


def _display_meta(display_text: str, ui_kind: str = "") -> dict:
    text = str(display_text or "").strip()
    kind = str(ui_kind or "").strip()
    if not text and not kind:
        return {}
    ui: dict = {}
    if text:
        ui["display_text"] = text
    if kind:
        ui["kind"] = kind
    return {"ui": ui}


def _chat_with_optional_turn(llm, messages, *, tools=None, cancel_event=None, turn: int | None = None):
    kwargs = {"cancel_event": cancel_event}
    if tools is not None:
        kwargs["tools"] = tools
    if _chat_accepts_turn(llm):
        kwargs["turn"] = turn
    return llm.chat(messages, **kwargs)


def _chat_accepts_turn(llm) -> bool:
    try:
        parameters = inspect.signature(llm.chat).parameters
    except (TypeError, ValueError):
        return False
    return "turn" in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def _exhaust_generator(gen, on_chunk=None):
    """消费 generator 的所有 yield，返回其 return 值（Response）。"""
    try:
        while True:
            chunk = next(gen)
            if on_chunk:
                on_chunk(chunk)
    except StopIteration as e:
        return e.value


def _tune_judge_config(config):
    tuned = replace(
        config,
        temperature=0.0,
        max_tokens=min(int(config.max_tokens or 1200), 1200),
        thinking="disabled",
        thinking_budget=None,
    )
    return tuned
