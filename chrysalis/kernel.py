"""命令行入口和顶层模块装配。"""

import argparse
import json
import sys
import time

from chrysalis.agent_loop import AgentLoop
from chrysalis.config import AgentConfig
from chrysalis.evolve import SkillGenerator, crystallize_skill, safe_skill_name
from chrysalis.llm import DeepSeekChat
from chrysalis.memory import Memory
from chrysalis.progress import ProgressCallback, stderr_progress
from chrysalis.reflect import reflect_traces
from chrysalis.session import SessionContext
from chrysalis.skills import SkillLibrary
from chrysalis.tools import file_list
from chrysalis.trace import TraceRecorder

HELP_TEXT = """用法：
  chrysalis [--quiet] <任务>
  chrysalis --interactive
  python -m chrysalis.kernel [--quiet] <任务>

参数：
  <任务>        交给 agent 的任务
  -i, --interactive
                进入连续对话模式
  --quiet      不显示每轮进度摘要
  -h, --help   显示这段帮助
"""

EXIT_COMMANDS = {"/exit", "/quit", "exit", "quit", "退出", "再见"}


class Kernel:
    def __init__(
            self,
            config: AgentConfig | None = None,
            llm: DeepSeekChat | None = None,
            progress: ProgressCallback | None = None,
            skill_generator: SkillGenerator | None = None,
    ):
        self.config = config or AgentConfig()
        self.progress = progress
        self.skill_generator = skill_generator
        self.memory = Memory(self.config.memory_dir, self.config.memory_json)
        self.session = SessionContext()
        self.llm = llm or DeepSeekChat(self.config.llm)
        self.skills = SkillLibrary(self.config.skills_dir, self.memory)
        self.loop = AgentLoop(
            self.llm,
            self.memory,
            self.config.workspace_dir,
            self.skills,
            self.config.max_turns,
            progress=self.progress,
        )
        self.traces = TraceRecorder(self.config.trace_log)

    def run(self, task: str) -> dict:
        started = time.perf_counter()
        self._progress(f"开始任务：{task}")
        direct = self._try_direct_tool(task)
        if direct is not None:
            direct["elapsed_ms"] = _elapsed_ms(started)
            self._progress(f"本地直达：{direct.get('final', '已完成。')}")
            self._append_trace_safely(task, direct)
            self.session.remember(task, direct)
            return direct

        try:
            result = self.loop.run(task, session_context=self.session.context())
        except Exception as exc:
            result = {
                "ok": False,
                "error": str(exc),
                "final": f"任务执行异常：{exc}",
                "model_error": True,
                "exception_type": type(exc).__name__,
                "transcript": [],
            }

        if self._should_write_skill(task, result):
            result["skill_candidate"] = True
            try:
                skill = crystallize_skill(
                    self.config.skills_dir,
                    task,
                    result.get("transcript", []),
                    generator=self.skill_generator,
                )
            except Exception as exc:
                message = f"技能沉淀验证失败，已跳过写入：{exc}"
                result.setdefault("warnings", []).append(message)
                result["skill_validation_error"] = str(exc)
            else:
                self.memory.add_skill(skill.name, skill.description)
                result["skill"] = str(skill.path)
                result["skill_steps"] = skill.steps_count
                result["skill_generator"] = skill.generator
        result["elapsed_ms"] = _elapsed_ms(started)
        self._append_trace_safely(task, result)
        self.session.remember(task, result)
        return result

    def _progress(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message)

    def _try_direct_tool(self, task: str) -> dict | None:
        """少量本地自检任务不必请求模型。"""
        lower = task.lower()
        wants_list = any(word in lower for word in ("列出", "看看", "查看", "有哪些", "list"))
        mentions_workspace = any(word in lower for word in ("workspace", "工作目录"))
        if wants_list and mentions_workspace:
            data = file_list(".", self.config.workspace_dir)
            return {"ok": data.get("ok", False), "final": "已列出 workspace。", "data": data, "transcript": []}
        wants_dirs = any(word in lower for word in ("目录", "文件夹", "directories", "folders"))
        mentions_project = any(word in lower for word in ("本项目", "项目", "根目录", "project"))
        if wants_list and wants_dirs and mentions_project:
            data = file_list(".", self.config.root)
            directories = [item for item in data.get("entries", []) if item.get("type") == "dir"]
            names = "、".join(item["name"] for item in directories) or "无"
            data["directories"] = directories
            return {
                "ok": data.get("ok", False),
                "final": f"项目根目录包含这些目录：{names}。",
                "data": data,
                "transcript": [],
            }
        wants_reflect = any(word in lower for word in ("复盘", "反思", "reflect"))
        mentions_run = any(word in lower for word in ("运行", "trace", "轨迹", "最近"))
        if wants_reflect and mentions_run:
            return reflect_traces(self.config)
        return None

    def _append_trace_safely(self, task: str, result: dict) -> None:
        try:
            self.traces.append(task, result)
        except Exception as exc:
            result.setdefault("warnings", []).append(f"写入运行轨迹失败：{exc}")

    def _should_write_skill(self, task: str, result: dict) -> bool:
        """判断本轮是否允许沉淀 skill。

        对齐 GA 的克制原则：不是用户手动指定，而是由执行轨迹说明“值得记住”。
        只有长链路、多工具、未调用已有技能、且没有重复沉淀过的成功任务才会写入。
        """
        if not result.get("ok"):
            return False
        transcript = result.get("transcript", [])
        if not transcript:
            return False
        if any("skill" in item for item in transcript if isinstance(item, dict)):
            return False
        if self._skill_already_exists(task):
            return False

        turns = [int(item.get("turn", 0)) for item in transcript if isinstance(item, dict)]
        turn_count = max(turns, default=0)
        tool_calls = [item for item in transcript if isinstance(item, dict) and item.get("tool")]
        if turn_count < self.config.min_skill_turns:
            return False
        if len(tool_calls) < 3:
            return False
        return True

    def _skill_already_exists(self, task: str) -> bool:
        name = safe_skill_name(task)
        if (self.config.skills_dir / f"{name}.py").exists():
            return True
        return any(skill["name"] == name for skill in self.memory.list_skills())


def main() -> None:
    if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
        print(HELP_TEXT)
        return

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-i", "--interactive", action="store_true", help="进入连续对话模式")
    parser.add_argument("--quiet", action="store_true", help="不显示每轮进度摘要")
    parser.add_argument("task", nargs="*", help="交给 agent 的任务")
    args = parser.parse_args()

    task = " ".join(args.task).strip()
    if args.interactive:
        progress = None if args.quiet else stderr_progress
        run_interactive(Kernel(progress=progress))
        return

    if not task:
        raise SystemExit("用法：chrysalis <任务>")

    try:
        progress = None if args.quiet else stderr_progress
        result = Kernel(progress=progress).run(task)
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def run_interactive(kernel: Kernel, input_func=input, output_func=print, ) -> None:
    """运行一个最小交互循环：一行任务执行一次，直到用户退出。"""
    output_func("Chrysalis 交互模式。输入 /exit 或 退出 结束。")
    while True:
        try:
            task = input_func("chrysalis> ").strip()
        except EOFError:
            output_func("已退出。")
            return

        if not task:
            continue
        if task.lower() in EXIT_COMMANDS:
            output_func("已退出。")
            return

        try:
            result = kernel.run(task)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        output_func(format_interactive_result(result))


def format_interactive_result(result: dict) -> str:
    """把一次运行结果压成适合终端阅读的输出。"""
    if "final" in result:
        return str(result["final"])
    if "error" in result:
        return f"出错：{result['error']}"
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


if __name__ == "__main__":
    main()
