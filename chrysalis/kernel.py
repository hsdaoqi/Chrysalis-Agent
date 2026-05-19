"""命令行入口和顶层模块装配。"""

import argparse
import json
import sys
import time

from chrysalis.agent_loop import AgentLoop
from chrysalis import subagent
from chrysalis.task_queue import TaskQueue
from configs.config import AgentConfig
from chrysalis.llm import LLMClient, UsageTracker, create_client
from utils.progress import ProgressCallback, stderr_progress
from chrysalis.session import SessionContext

HELP_TEXT = """用法：
  chrysalis [--quiet] <任务>
  chrysalis --interactive
  chrysalis --tui
  python -m chrysalis.kernel [--quiet] <任务>

参数：
  <任务>        交给 agent 的任务
  -i, --interactive
                进入连续对话模式
  --tui        启动终端 UI 模式
  --quiet      不显示每轮进度摘要
  -h, --help   显示这段帮助
"""

EXIT_COMMANDS = {"/exit", "/quit", "exit", "quit", "退出", "再见"}
USER_ACTION_DONE_WORDS = ("已完成", "完成了", "弄好了", "操作好了", "已登录", "登录好了", "登录完成", "继续")
USER_ACTION_SKIP_WORDS = ("跳过", "不登录", "跳过登录", "换方案", "换公开", "公开来源", "不用这个")


class Kernel:
    def __init__(
            self,
            config: AgentConfig | None = None,
            llm: LLMClient | None = None,
            progress: ProgressCallback | None = None,
    ):
        self.config = config or AgentConfig()
        self.progress = progress
        #TODO 将session变为通过指令自己选要恢复哪一个对话
        self.session = SessionContext(
            persist_path=self.config.data_dir / "session.json",
        )
        self.session.load()
        self.tracker = UsageTracker(
            persist_path=self.config.data_dir / "usage_history.jsonl",
            pricing=self.config.llm.pricing_dict(),
        )
        self.llm = llm or create_client(self.config.llm.to_session_config(), tracker=self.tracker)
        self.pending_user_action: dict | None = None
        self.history: list[str] = []
        self.loop = AgentLoop(
            self.llm,
            self.config.workspace_dir,
            self.config.max_turns,
            progress=self.progress,
            history=self.history,
        )
        subagent.configure(
            session_config=self.config.llm.to_session_config(),
            progress=self.progress,
        )

    def run(self, task: str) -> dict:
        started = time.perf_counter()
        self.llm.reset_task_usage()
        run_task, extra_context = self._resolve_pending_user_action(task)
        self._progress(f"开始任务：{run_task}")

        try:
            session_context = self.session.context()
            if extra_context:
                session_context = (session_context + "\n\n" + extra_context).strip()
            result = self.loop.run(run_task, session_context=session_context)
        except Exception as exc:
            result = {
                "ok": False,
                "error": str(exc),
                "final": f"任务执行异常：{exc}",
            }
        if result.get("need_user"):
            self.pending_user_action = {
                "task": run_task,
                "question": result.get("question") or result.get("final", ""),
                "reason": result.get("reason", "need_user"),
            }

        result["elapsed_ms"] = _elapsed_ms(started)
        elapsed = result["elapsed_ms"]
        model = self.config.llm.model
        self.tracker.end_task(run_task[:100], elapsed, model)
        result["usage"] = self.tracker.task_usage_dict()
        result["usage"]["cost"] = self.tracker.task_cost(model)
        self.session.remember(run_task, result)
        return result

    def _progress(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message)

    def _resolve_pending_user_action(self, task: str) -> tuple[str, str]:
        if not self.pending_user_action:
            return task, ""
        normalized = task.strip().lower()
        pending = self.pending_user_action
        original_task = str(pending.get("task", task))
        if any(word in normalized for word in USER_ACTION_DONE_WORDS):
            self.pending_user_action = None
            return (
                original_task,
                "用户刚才已经完成此前需要的人为操作。请从当前状态继续原任务，不要重新从头搜索。",
            )
        if any(word in normalized for word in USER_ACTION_SKIP_WORDS):
            self.pending_user_action = None
            return (
                original_task,
                "用户选择跳过此前需要人为操作的路径。请避开该路径，改用其他可行方案继续原任务。",
            )
        return task, ""


def main() -> None:
    if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
        print(HELP_TEXT)
        return

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-i", "--interactive", action="store_true", help="进入连续对话模式")
    parser.add_argument("--tui", action="store_true", help="启动终端 UI 模式")
    parser.add_argument("--quiet", action="store_true", help="不显示每轮进度摘要")
    parser.add_argument("task", nargs="*", help="交给 agent 的任务")
    args = parser.parse_args()

    if args.tui:
        from chrysalis.tui import launch_tui
        launch_tui()
        return

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


QUEUE_COMMANDS = {"/queue", "/q"}
QUEUE_ADD_PREFIX = ("/add ", "/a ")


def run_interactive(kernel: Kernel, input_func=input, output_func=print, ) -> None:
    """运行一个最小交互循环：一行任务执行一次，直到用户退出。"""
    queue = TaskQueue(kernel.config.data_dir / "task_queue.json")
    output_func("Chrysalis 交互模式。输入 /exit 或 退出 结束。")
    pending = queue.pending_count()
    if pending:
        output_func(f"  队列中有 {pending} 个待处理任务。输入回车自动执行，或 /queue 查看。")

    while True:
        try:
            task = input_func("chrysalis> ").strip()
        except EOFError:
            output_func("已退出。")
            return

        if task.lower() in EXIT_COMMANDS:
            output_func("已退出。")
            return

        if task.lower() in QUEUE_COMMANDS:
            _show_queue(queue, output_func)
            continue

        if any(task.lower().startswith(p) for p in QUEUE_ADD_PREFIX):
            new_task = task.split(" ", 1)[1].strip() if " " in task else ""
            if new_task:
                queue.add(new_task)
                output_func(f"已添加到队列。当前待处理：{queue.pending_count()}")
            else:
                output_func("用法：/add <任务描述>")
            continue

        if not task:
            executed = _try_run_queued(kernel, queue, output_func)
            if not executed:
                continue
            continue

        try:
            result = kernel.run(task)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        output_func(format_interactive_result(result))


def _try_run_queued(kernel: Kernel, queue: TaskQueue, output_func) -> bool:
    """尝试从队列取一个 pending 任务执行。返回是否执行了任务。"""
    item = queue.next_pending()
    if item is None:
        return False
    index, task_data = item
    task_text = task_data.get("task", "")
    output_func(f"[队列] 执行任务：{task_text}")
    queue.mark_running(index)
    try:
        result = kernel.run(task_text)
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    if result.get("ok"):
        queue.mark_done(index, result.get("final", ""))
    else:
        queue.mark_failed(index, result.get("error", result.get("final", "")))
    output_func(format_interactive_result(result))
    remaining = queue.pending_count()
    if remaining:
        output_func(f"  队列剩余 {remaining} 个任务。回车继续执行。")
    return True


def _show_queue(queue: TaskQueue, output_func) -> None:
    tasks = queue.load()
    if not tasks:
        output_func("队列为空。")
        return
    for i, t in enumerate(tasks):
        status = t.get("status", "?")
        marker = {"pending": "[ ]", "running": "[>]", "done": "[x]", "failed": "[!]"}.get(status, "[?]")
        line = f"  {marker} {i+1}. {t.get('task', '')}"
        if status in ("done", "failed") and t.get("result"):
            line += f"  -> {t['result'][:60]}"
        output_func(line)


def format_interactive_result(result: dict) -> str:
    """把一次运行结果压成适合终端阅读的输出。"""
    parts: list[str] = []
    if "final" in result:
        parts.append(str(result["final"]))
    elif "error" in result:
        parts.append(f"出错：{result['error']}")
    else:
        parts.append(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    usage = result.get("usage")
    if usage and usage.get("total_tokens"):
        from chrysalis.llm.types import Usage, _fmt_num
        from chrysalis.llm.usage import _fmt_elapsed
        u = Usage.from_dict(usage)
        elapsed = result.get("elapsed_ms", 0)
        cost = usage.get("cost", 0)
        turns = usage.get("turns", 0)

        info_parts = [u.format()]
        if cost > 0:
            info_parts.append(f"~${cost:.4f}")
        if turns:
            info_parts.append(f"{turns} turns")
        if elapsed:
            info_parts.append(_fmt_elapsed(elapsed))
        parts.append(f"[{' | '.join(info_parts)}]")

    return "\n".join(parts)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


if __name__ == "__main__":
    main()
