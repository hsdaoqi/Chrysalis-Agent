"""受 GenericAgent 启发的极简观察-行动循环。"""

import json
from pathlib import Path

from chrysalis.llm import DeepSeekChat
from chrysalis.memory import Memory
from chrysalis.observation import compact_observation
from chrysalis.progress import ProgressCallback, summarize_action, summarize_observation
from chrysalis.skills import SkillLibrary
from chrysalis.tools import TOOL_PROMPT, dumps_observation, run_tool
from chrysalis.working import WorkingMemory


SYSTEM_PROMPT = """你是 Chrysalis，一个极简自主 agent。
需要证据或需要修改文件时，必须使用工具。
如果 L1 记忆里已有合适技能，优先调用技能。
每一步都保持很小。只能返回 JSON。"""


class AgentLoop:
    def __init__(
        self,
        llm: DeepSeekChat,
        memory: Memory,
        workspace: Path,
        skills: SkillLibrary | None = None,
        max_turns: int = 12,
        progress: ProgressCallback | None = None,
    ):
        self.llm = llm
        self.memory = memory
        self.workspace = workspace
        self.skills = skills
        self.max_turns = max_turns
        self.progress = progress
        self.working = WorkingMemory()

    def run(self, task: str, session_context: str = "") -> dict:
        self.working.reset()
        l1_context = self.memory.context()
        session_block = ("\n\n" + session_context.strip()) if session_context.strip() else ""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n## L1 记忆\n" + l1_context + session_block + "\n\n" + TOOL_PROMPT},
            {"role": "user", "content": f"任务：\n{task}"},
        ]
        transcript: list[dict] = []

        for turn in range(1, self.max_turns + 1):
            raw = self.llm.chat(messages).text.strip()
            action = _parse_json(raw)
            transcript.append({"turn": turn, "assistant": action or raw})
            self._progress(summarize_action(turn, action or raw))

            if not action:
                messages.append({"role": "user", "content": "JSON 无效。请返回工具调用 JSON，或返回 final JSON。"})
                continue

            if "final" in action:
                final = str(action["final"])
                memory_error = self._remember_safely(task, final)
                if memory_error:
                    transcript.append({"turn": turn, "memory_warning": memory_error})
                return {
                    "ok": True,
                    "final": final,
                    "transcript": transcript,
                }

            if "skill" in action:
                observation = self._run_skill(str(action["skill"]), task)
                compact = compact_observation(observation)
                self._progress(summarize_observation(turn, "技能", compact))
                transcript.append({
                    "turn": turn,
                    "skill": action["skill"],
                    "observation": compact,
                })
                messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
                messages.append({"role": "user", "content": "技能观察结果：\n" + dumps_observation(compact)})
                continue

            tool = action.get("tool")
            args = action.get("args") or {}
            if tool == "update_working_checkpoint":
                observation = self._update_working_checkpoint(args)
            elif tool == "start_long_term_update":
                observation = self._start_long_term_update(args)
            else:
                observation = run_tool(str(tool), args, self.workspace)
            compact = compact_observation(observation)
            self._progress(summarize_observation(turn, "工具", compact))
            transcript.append({"turn": turn, "tool": tool, "observation": compact})
            if isinstance(observation, dict) and observation.get("need_user"):
                return {
                    "ok": False,
                    "need_user": True,
                    "final": str(observation.get("question", "需要用户输入。")),
                    "question": observation.get("question", ""),
                    "candidates": observation.get("candidates", []),
                    "transcript": transcript,
                }
            messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
            messages.append({"role": "user", "content": self._next_prompt_with_checkpoint("观察结果：\n" + dumps_observation(compact))})

        final = "达到最大轮数，仍未得到最终回答。"
        memory_error = self._remember_safely(task, final)
        if memory_error:
            transcript.append({"turn": self.max_turns, "memory_warning": memory_error})
        return {"ok": False, "final": final, "transcript": transcript}

    def _run_skill(self, name: str, task: str) -> dict:
        if self.skills is None:
            return {"ok": False, "error": "当前没有配置技能库。"}
        return self.skills.execute(name, task)

    def _update_working_checkpoint(self, args: dict) -> dict:
        return self.working.update_checkpoint(
            key_info=str(args.get("key_info", "")),
            related_sop=str(args.get("related_sop", "")),
        )

    def _start_long_term_update(self, args: dict) -> dict:
        return self.working.request_long_term_update(reason=str(args.get("reason", "")))

    def _next_prompt_with_checkpoint(self, prompt: str) -> str:
        return self.working.append_to_prompt(prompt)

    def _remember_safely(self, task: str, final: str) -> str | None:
        try:
            self.memory.remember_episode(task, final)
        except Exception as exc:
            return f"写入最近任务摘要失败：{exc}"
        return None

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
