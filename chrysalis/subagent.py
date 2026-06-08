"""子 agent 派生：用独立上下文执行子任务，只返回紧凑结果给父 agent。

支持并发：传入多个任务时用线程池并行执行，阻塞直到全部完成。

进度/事件回传：每个子任务带 `sub_index` 身份，通过 `on_subagent_event` 实时回调，
前端可据此区分并展示各子任务的实时进度。

多 session 路由：`on_subagent_event` 通过 contextvar 按运行绑定（见 `bind_run`），
避免模块级单例在多 session 并发时把事件路由到错误的 session。
"""

import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from chrysalis.llm import create_client
from chrysalis.llm.types import SessionConfig

_session_config: SessionConfig | None = None
_progress = None
_on_subagent_event = None
_executor: ThreadPoolExecutor | None = None
_max_workers: int = 4
_subagent_threads: set[int] = set()
_threads_lock = threading.Lock()

# 每次运行（每个父 agent 调用线程）的回调绑定。run_tasks 优先读取这里，
# 没有绑定时回退到模块级默认值。这样多 session 并发各自隔离，事件不串台。
_run_binding: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "subagent_run_binding", default=None
)

SUBAGENT_MAX_TURNS = 10
SUBAGENT_SYSTEM_PROMPT = (
    "你是 Chrysalis 子任务 agent。专注完成指定任务，完成后立即返回 final。\n"
    "不要发散，不要询问用户，不要尝试派生子 agent。"
)


def configure(
    session_config: SessionConfig,
    progress=None,
    max_workers: int = 4,
    on_subagent_event=None,
) -> None:
    """配置子 agent 运行环境。

    `progress` / `on_subagent_event` 为模块级默认回调；并发多 session 时建议
    通过 `bind_run` 在每次运行内绑定带 session 身份的回调，而不是依赖这里的默认值。
    """
    global _session_config, _progress, _on_subagent_event, _executor, _max_workers
    _session_config = session_config
    _progress = progress
    _on_subagent_event = on_subagent_event
    if _executor is None or max_workers != _max_workers:
        old = _executor
        _executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="subagent")
        _max_workers = max_workers
        if old is not None:
            old.shutdown(wait=False)


def bind_run(progress=None, on_subagent_event=None):
    """在当前线程的运行上下文里绑定回调，返回 token 供 unbind_run 还原。

    由 Kernel.run 在任务 worker 线程上调用，使子任务事件路由到正确的 session。
    """
    return _run_binding.set({"progress": progress, "on_subagent_event": on_subagent_event})


def unbind_run(token) -> None:
    try:
        _run_binding.reset(token)
    except (ValueError, LookupError):
        pass


def _resolve_callbacks():
    binding = _run_binding.get()
    if binding is not None:
        return binding.get("progress"), binding.get("on_subagent_event")
    return _progress, _on_subagent_event


def run_tasks(tasks: list[dict], workspace: Path | None = None) -> dict:
    """并行执行多个子任务，阻塞直到全部完成。

    返回结构：
        {
            "ok": True,
            "results": [{"index", "task", "ok", "result", "error"}, ...],
            "summary": {"total", "succeeded", "failed"},
        }
    单个子任务失败被隔离，不影响其他子任务。
    """
    if _session_config is None:
        return {"ok": False, "error": "子 agent 未配置（缺少 SessionConfig）"}

    with _threads_lock:
        nested = threading.current_thread().ident in _subagent_threads
    if nested:
        return {"ok": False, "error": "子 agent 不允许再派生子 agent"}

    if not tasks:
        return {"ok": False, "error": "任务列表不能为空"}

    for i, t in enumerate(tasks):
        if not t.get("task", "").strip():
            return {"ok": False, "error": f"第 {i+1} 个子任务描述不能为空"}

    # 在父线程解析回调，显式传入线程闭包（contextvar 不会自动传播到线程池线程）。
    progress, on_event = _resolve_callbacks()

    if len(tasks) == 1:
        result = _run_subagent(0, tasks[0]["task"], workspace, tasks[0].get("context", ""), progress, on_event)
        results = [result]
    else:
        futures = {}
        for i, t in enumerate(tasks):
            future = _executor.submit(
                _run_in_thread, i, t["task"], workspace, t.get("context", ""), progress, on_event
            )
            futures[future] = i

        results: list[dict | None] = [None] * len(tasks)
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                results[idx] = {
                    "index": idx,
                    "task": tasks[idx].get("task", ""),
                    "ok": False,
                    "result": None,
                    "error": f"子任务异常: {exc}",
                }

    succeeded = sum(1 for r in results if r and r.get("ok"))
    failed = len(results) - succeeded
    return {
        "ok": True,
        "results": results,
        "summary": {"total": len(results), "succeeded": succeeded, "failed": failed},
    }


def _run_in_thread(idx: int, task: str, workspace: Path | None, context: str, progress, on_event) -> dict:
    tid = threading.current_thread().ident
    with _threads_lock:
        _subagent_threads.add(tid)
    try:
        return _run_subagent(idx, task, workspace, context, progress, on_event)
    finally:
        with _threads_lock:
            _subagent_threads.discard(tid)


def _run_subagent(idx: int, task: str, workspace: Path | None, context: str, progress, on_event) -> dict:
    from chrysalis.agent_loop import AgentLoop
    from chrysalis.tools import generate_tools_schema

    def emit(kind: str, **payload) -> None:
        if on_event is None:
            return
        try:
            on_event({"sub_index": idx, "task": task, "kind": kind, **payload})
        except Exception:
            pass

    base = {"index": idx, "task": task}

    try:
        child_llm = create_client(_session_config)

        child_progress = None
        if progress is not None or on_event is not None:
            def child_progress(msg: str) -> None:
                if progress is not None:
                    progress(f"[子任务{idx + 1}] {msg}")
                emit("progress", message=msg)

        on_tool_call = None
        if on_event is not None:
            def on_tool_call(tool: str, args: dict, observation: dict | None) -> None:
                if observation is None:
                    emit("tool_started", tool=tool, args=args)
                else:
                    emit("tool_completed", tool=tool, args=args, observation=observation)

        child_tools = generate_tools_schema(exclude={"spawn_subagent"})

        emit("started")

        loop = AgentLoop(
            llm=child_llm,
            workspace=workspace or Path("."),
            max_turns=SUBAGENT_MAX_TURNS,
            progress=child_progress,
            on_tool_call=on_tool_call,
            use_function_calling=True,
            tools_schema=child_tools,
        )

        result = loop.run(task, session_context=context)
    except Exception as exc:
        emit("done", ok=False, error=str(exc))
        return {**base, "ok": False, "result": None, "error": f"子任务异常: {exc}"}

    if result.get("need_user"):
        error = f"子任务需要用户输入：{result.get('question', '')}"
        emit("done", ok=False, error=error)
        return {**base, "ok": False, "result": None, "error": error}

    if result.get("ok"):
        final = result["final"]
        emit("done", ok=True)
        return {**base, "ok": True, "result": final, "error": None}

    error = result.get("final", "子任务失败")
    emit("done", ok=False, error=error)
    return {**base, "ok": False, "result": None, "error": error}
