"""受 GenericAgent 启发的极简观察-行动循环。"""

import json
from pathlib import Path
from typing import Callable

from chrysalis.llm import LLMClient
from chrysalis.observation import compact_observation
from utils.get_prompts import get_system_prompt
from utils.progress import ProgressCallback, summarize_action, summarize_observation
from chrysalis.tools import TOOL_PROMPT, TOOLS_SCHEMA, dumps_observation, run_tool
from chrysalis.working import WorkingMemory

HISTORY_WINDOW = 30
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
            use_function_calling: bool = True,
            tools_schema: list[dict] | None = None,
    ):
        self.llm = llm
        self.workspace = workspace
        self.max_turns = max_turns
        self.progress = progress
        self.on_stream_chunk = on_stream_chunk
        self.on_tool_call = on_tool_call
        self.use_function_calling = use_function_calling
        self.tools_schema = tools_schema
        self.working = WorkingMemory()
        self.history_info: list[str] = history if history is not None else []

    def run(self, task: str, session_context: str = "") -> dict:
        self.working.reset()
        self.history_info.append(f"[USER]: {_brief(task, 200)}")

        session_block = ("\n\n" + session_context.strip()) if session_context.strip() else ""
        system_prompt = get_system_prompt()

        if self.use_function_calling:
            return self._run_function_calling(task, system_prompt, session_block)
        else:
            return self._run_json_in_text(task, system_prompt, session_block)

    # ── Native Function Calling 模式 ──

    def _run_function_calling(self, task: str, system_prompt: str, session_block: str) -> dict:
        system = system_prompt + "\n\n## L1 记忆\n" + session_block
        tools = self.tools_schema or TOOLS_SCHEMA

        # 设置 system prompt 和 tools
        self.llm.set_system(system)
        self.llm.set_tools(tools)

        # 首条 user message
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]

        for turn in range(1, self.max_turns + 1):
            response = _exhaust_generator(
                self.llm.chat(messages, tools=tools),
                self.on_stream_chunk,
            )

            # 模型返回了 tool_calls
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
                observation = run_tool(tool_name, args, self.workspace)
                if self.on_tool_call:
                    self.on_tool_call(tool_name, args, observation)

                self._handle_agent_tool_side_effects(observation)
                compact = compact_observation(observation)
                self._progress(summarize_observation(turn, "工具", compact))

                if isinstance(observation, dict) and observation.get("need_user"):
                    return {
                        "ok": False,
                        "need_user": True,
                        "final": str(observation.get("question", "需要用户输入。")),
                        "question": observation.get("question", ""),
                        "candidates": observation.get("candidates", []),
                        "reason": observation.get("reason", "need_user"),
                    }

                # 下一轮：传 tool_result 回去
                obs_text = dumps_observation(compact)
                messages = [{
                    "role": "user",
                    "content": obs_text,
                    "tool_results": [{
                        "tool_use_id": tc.id,
                        "content": obs_text,
                    }],
                }]
                continue

            # 模型返回了文本（最终回答）
            content = response.content.strip()
            if content:
                self._progress(summarize_action(turn, {"final": content}))
                self.history_info.append(f"[Agent] {_brief(content, MAX_SUMMARY_LEN)}")
                return {"ok": True, "final": content}

            # 空响应
            messages = [{"role": "user", "content": "请继续执行任务或给出最终回答。"}]

        return {"ok": False, "final": "达到最大轮数，仍未得到最终回答。"}

    # ── JSON-in-text 模式（fallback） ──

    def _run_json_in_text(self, task: str, system_prompt: str, session_block: str) -> dict:
        tool_prompt = TOOL_PROMPT
        messages = [
            {"role": "system", "content": system_prompt + "\n\n## L1 记忆\n" + session_block + "\n\n" + tool_prompt},
            {"role": "user", "content": f"任务：\n{task}"},
        ]

        for turn in range(1, self.max_turns + 1):
            response = _exhaust_generator(self.llm.chat(messages), self.on_stream_chunk)
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
            observation = run_tool(str(tool), args, self.workspace)
            if self.on_tool_call:
                self.on_tool_call(str(tool), args, observation)
            self._handle_agent_tool_side_effects(observation)
            compact = compact_observation(observation)
            self._progress(summarize_observation(turn, "工具", compact))
            if isinstance(observation, dict) and observation.get("need_user"):
                return {
                    "ok": False,
                    "need_user": True,
                    "final": str(observation.get("question", "需要用户输入。")),
                    "question": observation.get("question", ""),
                    "candidates": observation.get("candidates", []),
                    "reason": observation.get("reason", "need_user"),
                }
            obs_text = "观察结果：\n" + dumps_observation(compact)
            messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
            messages.append({"role": "user",
                             "content": self._next_prompt_with_anchor(obs_text)})

        return {"ok": False, "final": "达到最大轮数，仍未得到最终回答。"}

    # ── History ──

    def _append_history_from_action(self, action: dict | None, raw: str) -> None:
        if not action:
            summary = "JSON解析失败"
        elif "final" in action:
            summary = _brief(str(action["final"]), MAX_SUMMARY_LEN)
        elif "tool" in action:
            tool = action.get("tool", "")
            args = {k: v for k, v in (action.get("args") or {}).items() if not str(k).startswith("_")}
            args_str = _brief(json.dumps(args, ensure_ascii=False), 50)
            summary = f"调用工具 {tool}, args: {args_str}"
        else:
            summary = "直接回答"
        self.history_info.append(f"[Agent] {summary}")

    def _get_anchor_prompt(self) -> str:
        h = self.history_info
        earlier = ""
        if len(h) > HISTORY_WINDOW:
            earlier = f"<earlier_context>\n{self._fold_earlier(h[:-HISTORY_WINDOW])}\n</earlier_context>\n"
        h_str = "\n".join(h[-HISTORY_WINDOW:])
        prompt = f"\n### [WORKING MEMORY]\n{earlier}<history>\n{h_str}\n</history>"
        if self.working.key_info:
            prompt += f"\n<key_info>{self.working.key_info}</key_info>"
        if self.working.related_sop:
            prompt += f"\n有不清晰的地方请再次读取 {self.working.related_sop}"
        return prompt

    def _fold_earlier(self, lines: list[str]) -> str:
        parts: list[str] = []
        cnt = 0
        last = ""

        def flush():
            nonlocal cnt, last
            if cnt:
                if "直接回答" in last:
                    parts.append(f"[Agent]（{cnt} turns）")
                else:
                    parts.append(f"{last}（{cnt} turns）")

        for line in lines:
            if line.startswith("[USER]"):
                flush()
                parts.append(line)
                cnt = 0
                last = ""
            else:
                cnt += 1
                last = line
        flush()
        return "\n".join(parts[-150:])

    def _next_prompt_with_anchor(self, prompt: str) -> str:
        anchor = self._get_anchor_prompt()
        working_prompt = self.working.to_prompt()
        result = prompt + anchor
        if working_prompt:
            result += "\n\n" + working_prompt
        return result

    # ── Side effects ──

    def _handle_agent_tool_side_effects(self, observation: dict) -> None:
        if not isinstance(observation, dict):
            return
        if observation.get("_checkpoint"):
            self.working.update_checkpoint(
                key_info=str(observation.get("key_info", "")),
                related_sop=str(observation.get("related_sop", "")),
            )
        if observation.get("_long_term"):
            self.working.request_long_term_update(
                reason=str(observation.get("reason", "")),
            )

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


def _brief(text: str, max_len: int = 80) -> str:
    text = text.replace("\n", " ").strip()
    return text[:max_len] + "..." if len(text) > max_len else text


def _exhaust_generator(gen, on_chunk=None):
    """消费 generator 的所有 yield，返回其 return 值（Response）。"""
    try:
        while True:
            chunk = next(gen)
            if on_chunk:
                on_chunk(chunk)
    except StopIteration as e:
        return e.value
