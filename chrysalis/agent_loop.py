"""GenericAgent 的核心行动循环。"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

from chrysalis.context_engine import ContextEngine
from chrysalis.hooks import DisabledHookManager, HookContext, HookManager
from chrysalis.llm import LLMClient
from chrysalis.observation import compact_observation
from chrysalis.permission import FullAccessPermissionEngine, PermissionEngine
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
        on_permission_request: "Callable[[dict], str] | None" = None,
        on_thinking: "Callable[[str], None] | None" = None,
        on_working_change: "Callable[[dict], None] | None" = None,
        use_function_calling: bool = True,
        tools_schema: list[dict] | None = None,
        context_engine: ContextEngine | None = None,
        permission_engine: PermissionEngine | None = None,
        hooks: HookManager | None = None,
    ):
        self.llm = llm
        self.workspace = workspace
        self.max_turns = max_turns
        self.progress = progress
        self.on_stream_chunk = on_stream_chunk
        self.on_tool_call = on_tool_call
        self.on_permission_request = on_permission_request
        self.on_thinking = on_thinking
        self.on_working_change = on_working_change
        self.use_function_calling = use_function_calling
        self.tools_schema = tools_schema
        self.working = WorkingMemory()
        self.history_info: list[str] = history if history is not None else []
        self.context_engine = context_engine or ContextEngine()
        self.permission_engine = permission_engine or FullAccessPermissionEngine()
        self.hooks = hooks or DisabledHookManager()
        self._cancel_event = threading.Event()

    def run(self, task: str, session_context: str = "") -> dict:
        self._cancel_event.clear()
        self.working.reset()
        self.history_info.append(f"[USER]: {brief_text(task, 400)}")

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
            return {"ok": False, "blocked": True, "final": permission.reason, "permission": permission.to_result()}
        if permission.needs_user:
            return permission.to_result()

        system_prompt = get_system_prompt(include_memory=False)
        assembled = self.context_engine.assemble(
            base_system=system_prompt,
            task=task,
            working=self.working,
            history_lines=self.history_info,
            session_context=session_context,
            include_history_anchor=False,
        )

        if self.use_function_calling:
            result = self._run_function_calling(task, assembled.system, session_context)
        else:
            result = self._run_json_in_text(task, assembled.system, session_context)

        after_task = self.hooks.emit("after_task", HookContext(
            event="after_task",
            task=task,
            session_context=session_context,
            workspace=self.workspace,
            result=result,
        ))
        if after_task.stop:
            return {"ok": False, "blocked": True, "final": after_task.message or "task stopped by hook"}
        return result

    def cancel(self) -> None:
        self._cancel_event.set()
        if hasattr(self.llm, "cancel"):
            self.llm.cancel()

    def _cancelled_result(self) -> dict:
        return {"ok": False, "cancelled": True, "final": "任务已中断"}

    def _run_function_calling(self, task: str, system: str, session_context: str = "") -> dict:
        tools = self.tools_schema or TOOLS_SCHEMA
        self.llm.set_system(system)
        self.llm.set_tools(tools)

        messages = [{"role": "user", "content": task}]
        for turn in range(1, self.max_turns + 1):
            if self._cancel_event.is_set():
                return self._cancelled_result()

            response = _exhaust_generator(
                self.llm.chat(messages, tools=tools, cancel_event=self._cancel_event),
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

                if self.on_tool_call:
                    self.on_tool_call(tool_name, args, None)
                if self._cancel_event.is_set():
                    return self._cancelled_result()

                observation = self._execute_tool_with_guards(
                    task=task,
                    tool_name=tool_name,
                    args=args,
                    turn=turn,
                    session_context=session_context,
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
                self.history_info.append(f"[Agent] {brief_text(content, MAX_SUMMARY_LEN)}")
                return {"ok": True, "final": content}

            messages = [{"role": "user", "content": "请继续执行任务或给出最终回答。"}]

        return {"ok": False, "final": "达到最大轮数，仍未得到最终回答。"}

    def _run_json_in_text(self, task: str, system_prompt: str, session_context: str = "") -> dict:
        tool_prompt = TOOL_PROMPT
        messages = [
            {"role": "system", "content": system_prompt + "\n\n" + tool_prompt},
            {"role": "user", "content": f"任务：\n{task}"},
        ]

        for turn in range(1, self.max_turns + 1):
            if self._cancel_event.is_set():
                return self._cancelled_result()

            response = _exhaust_generator(self.llm.chat(messages, cancel_event=self._cancel_event), self.on_stream_chunk)
            if response.cancelled or self._cancel_event.is_set():
                return self._cancelled_result()

            raw = response.content.strip()
            action = _parse_json(raw)
            self._progress(summarize_action(turn, action or raw))
            self._append_history_from_action(action, raw)

            if not action:
                messages.append({"role": "user", "content": "JSON 无效。请返回工具调用 JSON，或返回 final JSON。"})
                continue

            if "final" in action:
                return {"ok": True, "final": str(action["final"])}

            tool = action.get("tool")
            args = action.get("args") or {}
            if self.on_tool_call:
                self.on_tool_call(str(tool), args, None)
            if self._cancel_event.is_set():
                return self._cancelled_result()

            tool_name = str(tool)
            observation = self._execute_tool_with_guards(
                task=task,
                tool_name=tool_name,
                args=args,
                turn=turn,
                session_context=session_context,
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

    def _append_history_from_action(self, action: dict | None, raw: str) -> None:
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
        self.history_info.append(f"[Agent] {summary}")

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
        return prompt + self._get_anchor_prompt()

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
            self.on_working_change(self.working.todo_snapshot())

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
            resolved = self._resolve_permission_request(permission.to_result())
            action = str(resolved.get("action", ""))
            if action == "allow":
                permission = self.permission_engine.assess_tool(
                    tool_name,
                    args,
                    workspace=self.workspace,
                    session_context=str(resolved.get("context", "")),
                )
            elif action == "deny":
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
            observation = run_tool(tool_name, args, self.workspace)
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


def _exhaust_generator(gen, on_chunk=None):
    """消费 generator 的所有 yield，返回其 return 值（Response）。"""
    try:
        while True:
            chunk = next(gen)
            if on_chunk:
                on_chunk(chunk)
    except StopIteration as e:
        return e.value
