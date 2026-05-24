"""命令行入口和顶层模块装配。"""

import argparse
import json
import sys
import time

from chrysalis.agent_loop import AgentLoop
from chrysalis import subagent
from chrysalis.hooks import HookManager
from chrysalis.permission import PermissionEngine
from chrysalis.task_queue import TaskQueue
from configs.config import AgentConfig
from chrysalis.llm import LLMClient, UsageTracker, create_client
from chrysalis.session_store import SessionStore
from utils.progress import ProgressCallback, stderr_progress

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
SESSION_COMMANDS = {"/session", "/sessions", "/s"}
USER_ACTION_DONE_WORDS = ("已完成", "完成了", "弄好了", "操作好了", "已登录", "登录好了", "登录完成", "继续")
USER_ACTION_SKIP_WORDS = ("跳过", "不登录", "跳过登录", "换方案", "换公开", "公开来源", "不用这个")


class Kernel:
    def __init__(
            self,
            config: AgentConfig | None = None,
            llm: LLMClient | None = None,
            progress: ProgressCallback | None = None,
            session_id: str | None = None,
    ):
        self.config = config or AgentConfig()  # 全局配置，路径、模型、turn 数等
        self.progress = progress  # 进度回调，CLI/TUI 用来显示状态
        self.session_store = SessionStore(self.config.data_dir / "sessions")  # 持久化 canonical history
        self.tracker = UsageTracker(  # token/费用/turn 统计
            persist_path=self.config.data_dir / "usage_history.jsonl",
            pricing=self.config.llm.pricing_dict(),
        )
        self.llm = llm or create_client(  # LLMClient，AgentLoop 只通过它和模型说话
            self.config.load_session_configs(),
            tracker=self.tracker,
            on_history_changed=self.session_store.save,
        )
        if llm and not llm._on_history_changed:
            llm._on_history_changed = self.session_store.save
        self.pending_user_action: dict | None = None  # 记录 ask_user 等待用户操作后的续跑状态
        self.history: list[str] = []  # 轻量 session anchor 文本历史
        self.loop = AgentLoop(  # 真正执行观察-行动循环的 AgentLoop
            self.llm,
            self.config.workspace_dir,
            self.config.max_turns,
            progress=self.progress,
            history=self.history,
        )
        self.permission_engine = self.loop.permission_engine
        self.hooks = self.loop.hooks
        if session_id:
            self.load_session(session_id)
        else:
            self.session_store.new_session(model=self.active_model_name)
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
            result = self.loop.run(run_task, session_context=extra_context)
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
        model = self.active_model_name
        self.tracker.end_task(run_task[:100], elapsed, model)
        result["usage"] = self.tracker.task_usage_dict()
        result["usage"]["cost"] = self.tracker.task_cost(model)
        return result

    def cancel(self) -> None:
        if hasattr(self.loop, "cancel"):
            self.loop.cancel()
        if hasattr(self.llm, "cancel"):
            self.llm.cancel()

    @property
    def active_model_name(self) -> str:
        session = self.llm.session
        if hasattr(session, "sessions") and session.sessions:
            return session.sessions[session._current_idx].config.name
        return session.config.name

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

    # ── Session management ──

    def load_session(self, session_id: str) -> None:
        history = self.session_store.load(session_id)
        with self.llm.session._lock:
            self.llm.session.history = history
        self.history.clear()
        self.pending_user_action = None

    def new_session(self) -> str:
        with self.llm.session._lock:
            self.llm.session.history.clear()
        self.history.clear()
        self.pending_user_action = None
        return self.session_store.new_session(model=self.active_model_name)

    def list_sessions(self) -> list[dict]:
        return self.session_store.list_sessions()

    def delete_session(self, session_id: str) -> bool:
        return self.session_store.delete(session_id)


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
    output_func("  /session — 管理会话  /queue — 管理任务队列")
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

        cmd_word = task.split()[0].lower() if task else ""
        if cmd_word in SESSION_COMMANDS:
            _handle_session_command(kernel, task, output_func)
            continue

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
        line = f"  {marker} {i + 1}. {t.get('task', '')}"
        if status in ("done", "failed") and t.get("result"):
            line += f"  -> {t['result'][:60]}"
        output_func(line)


def _handle_session_command(kernel: Kernel, raw: str, output_func) -> None:
    parts = raw.strip().split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""

    if sub == "new":
        sid = kernel.new_session()
        output_func(f"已创建新会话：{sid}")
        return

    if sub == "load":
        sessions = kernel.list_sessions()
        if not sessions:
            output_func("没有可加载的会话。")
            return
        try:
            idx = int(arg) - 1
        except (ValueError, TypeError):
            output_func("用法：/session load <编号>")
            return
        if idx < 0 or idx >= len(sessions):
            output_func(f"编号无效，范围 1-{len(sessions)}")
            return
        s = sessions[idx]
        kernel.load_session(s["id"])
        output_func(f"已加载会话：{s['title']} ({s['turns']} turns)")
        return

    if sub == "delete":
        sessions = kernel.list_sessions()
        if not sessions:
            output_func("没有可删除的会话。")
            return
        try:
            idx = int(arg) - 1
        except (ValueError, TypeError):
            output_func("用法：/session delete <编号>")
            return
        if idx < 0 or idx >= len(sessions):
            output_func(f"编号无效，范围 1-{len(sessions)}")
            return
        s = sessions[idx]
        kernel.delete_session(s["id"])
        output_func(f"已删除会话：{s['title']}")
        return

    sessions = kernel.list_sessions()
    if not sessions:
        output_func("暂无会话记录。")
        output_func("  /session new — 新建会话")
        return
    current = kernel.session_store.current_id
    output_func("会话列表：")
    for i, s in enumerate(sessions, 1):
        marker = " *" if s["id"] == current else ""
        output_func(f"  {i}. {s['title']}  [{s['model']}] {s['turns']}t  {s['updated_at']}{marker}")
    output_func("")
    output_func("  /session load <编号> — 加载  /session new — 新建  /session delete <编号> — 删除")


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
