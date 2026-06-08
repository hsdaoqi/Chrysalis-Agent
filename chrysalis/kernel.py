"""命令行入口和顶层模块装配。"""

import argparse
import json
import sys
import time
from pathlib import Path

from chrysalis.agent_loop import AgentLoop
from chrysalis import subagent
from chrysalis.hooks import HookManager
from chrysalis.memory import MemoryReviewStore
from chrysalis.permission import FullAccessPermissionEngine, PermissionEngine
from chrysalis.task_queue import TaskQueue
from chrysalis.gateway.bootstrap import (
    format_launch_summary,
    normalize_gateway_platforms,
    run_connect_cli,
    start_gateway_process,
)
from configs.config import AgentConfig
from chrysalis.llm import LLMClient, UsageTracker, create_client
from chrysalis.session_store import SessionStore
from utils.progress import ProgressCallback, stderr_progress

HELP_TEXT = """用法：
  chrysalis [--quiet] <任务>
  chrysalis connect [wechat|qq|feishu] [--background]
  chrysalis --interactive
  chrysalis --tui
  python -m chrysalis.kernel [--quiet] <任务>

参数：
  <任务>        交给 agent 的任务
  connect      启动消息网关并准备微信/QQ/飞书接入
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
QUEUE_COMMANDS = {"/queue", "/q"}
QUEUE_ADD_PREFIX = ("/add ", "/a ")
CRON_COMMANDS = {"/cron", "cron"}
PERMISSION_COMMANDS = {"/permissions", "/permission", "/perm"}
CONNECT_COMMANDS = {"connect", "/connect"}


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
        self.resume_state: dict | None = None  # 任务被中断后，注入此处用于从断点续跑
        self._resume_prompt_internal = False
        self.on_subagent_event = None  # 子任务实时事件回调，由桌面运行时按 session 注入
        self.history: list[str] = []  # 轻量 session anchor 文本历史
        self.loop = AgentLoop(  # 真正执行观察-行动循环的 AgentLoop
            self.llm,
            self.config.workspace_dir,
            self.config.max_turns,
            progress=self.progress,
            history=self.history,
            permission_engine=self._create_permission_engine(),
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
            max_workers=self.config.subagent_max_workers,
        )

    def _create_permission_engine(self) -> PermissionEngine:
        if self.config.permission_level.strip().lower() in {"full", "trusted", "off", "none"}:
            return FullAccessPermissionEngine()
        return PermissionEngine(
            level=self.config.permission_level,
            store_path=self.config.permissions_json,
        )

    def set_permission_level(self, level: str) -> None:
        self.config.permission_level = level
        self.loop.permission_engine = self._create_permission_engine()
        self.permission_engine = self.loop.permission_engine

    def run(self, task: str, session_context: str = "", images: list[dict] | None = None) -> dict:
        started = time.perf_counter()
        self.llm.reset_task_usage()
        run_task, extra_context, immediate = self._resolve_pending_user_action(task)
        if immediate is not None:
            immediate["elapsed_ms"] = _elapsed_ms(started)
            immediate["usage"] = self.tracker.task_usage_dict()
            immediate["context"] = self.llm.context_usage()
            return immediate
        ui_kind = "continue_prompt" if self._resume_prompt_internal else ""
        self._resume_prompt_internal = False
        merged_context = "\n\n".join(part for part in (session_context, extra_context) if part)
        resume = self.resume_state
        self.resume_state = None
        if resume:
            ui_kind = "continue_prompt"
        self._progress(f"开始任务：{run_task}")

        bind_token = subagent.bind_run(progress=self.progress, on_subagent_event=self.on_subagent_event)
        try:
            result = self.loop.run(
                run_task,
                session_context=merged_context,
                images=images,
                ui_kind=ui_kind,
                resume=resume,
            )
        except Exception as exc:
            result = {
                "ok": False,
                "error": str(exc),
                "final": f"任务执行异常：{exc}",
            }
        finally:
            subagent.unbind_run(bind_token)
        if result.get("need_user"):
            self.pending_user_action = {
                "task": run_task,
                "question": result.get("question") or result.get("final", ""),
                "reason": result.get("reason", "need_user"),
                "result": result,
            }
        self._persist_checkpoint(result, run_task)

        result["elapsed_ms"] = _elapsed_ms(started)
        elapsed = result["elapsed_ms"]
        model = self.active_model_name
        self.tracker.end_task(run_task[:100], elapsed, model)
        result["usage"] = self.tracker.task_usage_dict()
        result["usage"]["cost"] = self.tracker.task_cost(model)
        result["context"] = self.llm.context_usage()
        self._capture_memory_review(run_task, result)
        return result

    def cancel(self) -> None:
        if hasattr(self.loop, "cancel"):
            self.loop.cancel()
        if hasattr(self.llm, "cancel"):
            self.llm.cancel()

    def _persist_checkpoint(self, result: dict, task: str) -> None:
        """任务被中断且带 checkpoint 时落盘；其余情况清掉旧 checkpoint。"""
        session_id = self.session_store.current_id or ""
        if not session_id:
            return
        checkpoint = result.get("checkpoint") if isinstance(result, dict) else None
        if result.get("cancelled") and isinstance(checkpoint, dict):
            checkpoint = dict(checkpoint)
            checkpoint["task"] = task
            self.session_store.save_checkpoint(session_id, checkpoint)
            result["resumable"] = True
            result.pop("checkpoint", None)  # 已落盘，无需随事件回传整份状态
        else:
            # 正常完成 / need_user / 异常：丢弃此前的断点，避免续跑到陈旧状态
            self.session_store.delete_checkpoint(session_id)

    def has_checkpoint(self, session_id: str | None = None) -> bool:
        sid = session_id or self.session_store.current_id or ""
        return self.session_store.has_checkpoint(sid)

    def resume(self, session_context: str = "") -> dict:
        """从当前会话的 checkpoint 续跑。无 checkpoint 时返回错误结果。"""
        session_id = self.session_store.current_id or ""
        checkpoint = self.session_store.load_checkpoint(session_id) if session_id else None
        if not checkpoint:
            return {"ok": False, "error": "no_checkpoint", "final": "没有可续跑的断点。"}
        task = str(checkpoint.get("task") or "").strip() or "继续之前被中断的任务"
        self.resume_state = checkpoint
        return self.run(task, session_context=session_context)

    def guide(self, text: str) -> bool:
        if hasattr(self.loop, "guide"):
            return bool(self.loop.guide(text))
        return False

    @property
    def active_model_name(self) -> str:
        session = self.llm.session
        if hasattr(session, "sessions") and session.sessions:
            return session.sessions[session._current_idx].config.name
        return session.config.name

    def _progress(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message)

    def _capture_memory_review(self, task: str, result: dict) -> None:
        decision = result.get("memory_decision")
        if not isinstance(decision, dict):
            return
        target = str(decision.get("target") or "").strip().lower()
        if target not in {"fact", "user_profile"} or not bool(decision.get("should_persist")):
            return
        try:
            store = MemoryReviewStore(self.config.data_dir / "memory_reviews.json", self.config.memory_dir)
            item = store.create_from_decision(
                task=task,
                result=result,
                decision=decision,
                working=self.loop.working,
                session_id=self.session_store.current_id or "",
            )
            if item:
                result["memory_review_item"] = item
        except Exception as exc:
            result["memory_review_error"] = f"{type(exc).__name__}: {exc}"

    def _resolve_pending_user_action(self, task: str) -> tuple[str, str, dict | None]:
        if not self.pending_user_action:
            return task, "", None
        normalized = task.strip().lower()
        pending = self.pending_user_action
        original_task = str(pending.get("task", task))
        if pending.get("result", {}).get("permission_request"):
            resolved = self.permission_engine.resolve_user_choice(pending["result"], task)
            action = resolved.get("action")
            if action == "allow":
                self.pending_user_action = None
                self._resume_prompt_internal = True
                return original_task, str(resolved.get("context", "")), None
            if action == "deny":
                self.pending_user_action = None
                self._resume_prompt_internal = True
                return (
                    original_task,
                    "用户拒绝了此前的权限请求。请不要执行该操作，换一个不需要该权限的方案；如果没有可行方案，请说明原因。",
                    None,
                )
            if action == "detail":
                return "", "", {
                    "ok": False,
                    "need_user": True,
                    "permission_request": True,
                    "final": str(resolved.get("context", "")),
                    "question": pending.get("question", ""),
                    "options": pending["result"].get("options", []),
                    "candidates": pending["result"].get("candidates", []),
                    "reason": pending.get("reason", "permission_request"),
                }
        if any(word in normalized for word in USER_ACTION_DONE_WORDS):
            self.pending_user_action = None
            self._resume_prompt_internal = True
            return (
                original_task,
                "用户刚才已经完成此前需要的人为操作。请从当前状态继续原任务，不要重新从头搜索。",
                None,
            )
        if any(word in normalized for word in USER_ACTION_SKIP_WORDS):
            self.pending_user_action = None
            self._resume_prompt_internal = True
            return (
                original_task,
                "用户选择跳过此前需要人为操作的路径。请避开该路径，改用其他可行方案继续原任务。",
                None,
            )
        self.pending_user_action = None
        self._resume_prompt_internal = True
        question = str(pending.get("question") or "").strip()
        answer = task.strip()
        context_parts = [
            "用户刚才回答了此前 ask_user 问题。请基于这个回答继续原任务，不要把它当成一个新任务，也不要重复询问相同问题。",
        ]
        if question:
            context_parts.append(f"此前问题：{question}")
        if answer:
            context_parts.append(f"用户回答：{answer}")
        return original_task, "\n".join(context_parts), None

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
        self.resume_state = None
        return self.session_store.new_session(model=self.active_model_name)

    def list_sessions(self) -> list[dict]:
        return self.session_store.list_sessions()

    def delete_session(self, session_id: str) -> bool:
        return self.session_store.delete(session_id)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1].lower() in CONNECT_COMMANDS:
        run_connect_cli(sys.argv[2:])
        return

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
    if args.task and args.task[0].lower() == "cron":
        progress = None if args.quiet else stderr_progress
        kernel = Kernel(progress=progress)
        _handle_cron_command(kernel, " ".join(args.task), print)
        return

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
    queue = TaskQueue(kernel.config.data_dir / "task_queue.json")
    output_func("Chrysalis 交互模式。输入 /exit 或 退出 结束。")
    output_func("  /session — 管理会话  /queue — 管理任务队列  /connect wechat|qq|feishu — 启动消息网关")
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

        if cmd_word in CRON_COMMANDS:
            _handle_cron_command(kernel, task, output_func)
            continue

        if cmd_word in PERMISSION_COMMANDS:
            _handle_permission_command(kernel, task, output_func)
            continue

        if cmd_word in CONNECT_COMMANDS:
            _handle_connect_command(task, output_func)
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


def _handle_permission_command(kernel: Kernel, raw: str, output_func) -> None:
    parts = raw.strip().split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else "status"
    level = kernel.permission_engine.level
    if sub in {"status", "list", "ls", ""}:
        grants = kernel.permission_engine.store.list_grants()
        output_func(f"权限等级：{level}  (locked / balanced / full)")
        output_func(f"永久授权：{len(grants)} 条")
        for index, grant in enumerate(grants, 1):
            output_func(
                f"  {index}. [{grant.get('risk', '?')}] "
                f"{grant.get('tool') or grant.get('kind')}: {grant.get('summary', '')}"
            )
        if not grants:
            output_func("  暂无永久授权。")
        return
    output_func("用法：/permissions  查看权限等级和永久授权")


def _handle_connect_command(raw: str, output_func) -> None:
    parts = raw.strip().split()
    if not parts:
        parts = ["connect", "wechat"]
    start_index = 1 if parts[0].lower() in CONNECT_COMMANDS else 0
    platform_parts = [part for part in parts[start_index:] if not part.startswith("-")]
    try:
        platforms = normalize_gateway_platforms(platform_parts or ["wechat"])
    except SystemExit as exc:
        output_func(str(exc))
        return
    hidden = "--hidden" in parts
    try:
        result = start_gateway_process(
            platforms,
            shared_groups="--shared-groups" in parts,
            visible=not hidden,
        )
    except SystemExit as exc:
        output_func(str(exc))
        return
    output_func(format_launch_summary(result))


def _handle_cron_command(kernel: Kernel, raw: str, output_func) -> None:
    """Manage local scheduled jobs.

    Minimal command forms:
      /cron list
      /cron tick
      /cron daemon [seconds]
      /cron run <id>
      /cron pause <id>
      /cron resume <id>
      /cron remove <id>
      /cron create '<json object>'
    """
    from chrysalis.cron.jobs import (
        CronError,
        create_job,
        list_jobs,
        load_job,
        pause_job,
        remove_job,
        resume_job,
    )
    from chrysalis.cron.scheduler import run_daemon, run_job, save_job_output, tick

    parts = raw.strip().split(maxsplit=2)
    if parts and parts[0].lower() in CRON_COMMANDS:
        parts = parts[1:]
    sub = parts[0].lower() if parts else "list"
    arg = parts[1] if len(parts) > 1 else ""

    try:
        if sub in {"list", "ls", ""}:
            jobs = list_jobs(kernel.config, include_disabled=True)
            if not jobs:
                output_func("暂无 cron 任务。")
                return
            for job in jobs:
                state = job.get("state", {})
                enabled = "on" if job.get("enabled", True) else "off"
                output_func(
                    f"{job.get('id')} [{enabled}] {job.get('name')} | "
                    f"{job.get('schedule_display')} | next={state.get('next_run_at')} | "
                    f"last={state.get('last_status')}"
                )
            return

        if sub == "tick":
            count = tick(kernel.config, verbose=True)
            output_func(f"cron tick 完成，执行 {count} 个任务。")
            return

        if sub == "daemon":
            interval = int(arg) if arg else 60
            run_daemon(kernel.config, interval=interval)
            return

        if sub == "create":
            if not arg:
                output_func('用法：/cron create {"id":"daily","schedule":{...},"prompt":"..."}')
                output_func("也可用：/cron create @path/to/job.json")
                return
            if arg.startswith("@"):
                spec_path = Path(arg[1:]).expanduser()
                if not spec_path.is_absolute():
                    spec_path = (kernel.config.root / spec_path).resolve()
                spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
            else:
                spec = json.loads(arg)
            job = create_job(
                kernel.config,
                schedule=spec["schedule"],
                prompt=str(spec.get("prompt") or ""),
                job_id=spec.get("id"),
                name=spec.get("name"),
                script=spec.get("script"),
                no_agent=bool(spec.get("no_agent", False)),
                context_from=spec.get("context_from") or [],
                workdir=spec.get("workdir"),
                deliver=spec.get("deliver", "local"),
                repeat_times=spec.get("repeat_times"),
                max_delay_minutes=spec.get("max_delay_minutes"),
            )
            output_func(f"已创建 cron 任务：{job['id']} next={job.get('state', {}).get('next_run_at')}")
            return

        if not arg:
            output_func("用法：/cron <run|pause|resume|remove> <id>")
            return

        if sub == "pause":
            job = pause_job(kernel.config, arg)
            output_func(f"已暂停：{job['id']}")
            return

        if sub == "resume":
            job = resume_job(kernel.config, arg)
            output_func(f"已恢复：{job['id']} next={job.get('state', {}).get('next_run_at')}")
            return

        if sub in {"remove", "rm", "delete"}:
            ok = remove_job(kernel.config, arg)
            output_func("已删除。" if ok else f"任务不存在：{arg}")
            return

        if sub == "run":
            job = load_job(kernel.config, arg)
            if not job:
                output_func(f"任务不存在：{arg}")
                return
            success, output_doc, final, error = run_job(kernel.config, job)
            path = save_job_output(kernel.config, job["id"], output_doc)
            output_func(f"手动执行完成：success={success} output={path}")
            if error:
                output_func(f"error={error}")
            if final:
                output_func(final)
            return

        output_func("未知 cron 命令。可用：list/create/tick/daemon/run/pause/resume/remove")
    except (CronError, json.JSONDecodeError, KeyError, ValueError) as exc:
        output_func(f"cron 命令错误：{exc}")


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

    context_line = format_context_usage(result.get("context"))
    if context_line:
        parts.append(context_line)

    return "\n".join(parts)


def format_context_usage(context: dict | None, width: int = 18) -> str:
    """把上下文占用统计格式化成终端友好的进度条。"""
    if not context:
        return ""
    budget_chars = int(context.get("budget_chars") or 0)
    chars = int(context.get("chars") or 0)
    if budget_chars <= 0:
        return ""

    ratio = max(0.0, min(1.0, chars / budget_chars))
    filled = min(width, int(round(ratio * width)))
    bar = "█" * filled + "░" * (width - filled)
    percent = int(round(ratio * 100))
    tokens = int(context.get("tokens_estimate") or 0)
    window = int(context.get("context_window") or 0)
    messages = int(context.get("messages") or 0)
    soft_pct = int(round(float(context.get("soft_ratio") or 0) * 100))
    hard_pct = int(round(float(context.get("hard_ratio") or 0) * 100))

    parts = [
        f"Context [{bar}] {percent}%",
        f"~{_fmt_short(tokens)}/{_fmt_short(window)} tok",
        f"{messages} msgs",
        f"soft {soft_pct}%",
        f"hard {hard_pct}%",
    ]

    compaction = context.get("last_compaction") or {}
    actions = []
    if compaction.get("micro"):
        actions.append("micro")
    if compaction.get("snip"):
        actions.append("snip")
    if compaction.get("full"):
        actions.append("full")
    if compaction.get("llm_full"):
        actions.append("llm")
    if compaction.get("reactive"):
        actions.append("reactive")
    archived = int(compaction.get("tool_results_archived") or 0)
    if archived:
        actions.append(f"archived {archived}")
    if actions:
        parts.append("compact: " + ",".join(actions))

    assembly = context.get("assembly") or {}
    sections = assembly.get("sections") if isinstance(assembly, dict) else []
    if isinstance(sections, list) and sections:
        top = []
        for section in sections[:4]:
            if not isinstance(section, dict):
                continue
            label = str(section.get("label") or section.get("name") or "").strip()
            used = int(section.get("used_chars") or 0)
            if label and used:
                top.append(f"{label}:{_fmt_short(used)}ch")
        if top:
            parts.append("ctx: " + ",".join(top))

    return "[" + " | ".join(parts) + "]"


def _fmt_short(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}k"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


if __name__ == "__main__":
    main()
