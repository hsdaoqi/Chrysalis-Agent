"""子 agent 派生：用独立上下文执行子任务，只返回紧凑结果给父 agent。

支持并发：传入多个任务时用线程池并行执行，阻塞直到全部完成。
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from chrysalis.llm import create_client
from chrysalis.llm.types import SessionConfig

_session_config: SessionConfig | None = None
_progress = None
_executor: ThreadPoolExecutor | None = None
_subagent_threads: set[int] = set()

SUBAGENT_MAX_TURNS = 10
SUBAGENT_SYSTEM_PROMPT = (
    "你是 Chrysalis 子任务 agent。专注完成指定任务，完成后立即返回 final。\n"
    "不要发散，不要询问用户，不要尝试派生子 agent。"
)


def configure(session_config: SessionConfig, progress=None, max_workers: int = 4) -> None:
    global _session_config, _progress, _executor
    _session_config = session_config
    _progress = progress
    _executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="subagent")


def run_tasks(tasks: list[dict], workspace: Path | None = None) -> dict:
    """并行执行多个子任务，阻塞直到全部完成。"""
    if _session_config is None:
        return {"ok": False, "error": "子 agent 未配置（缺少 SessionConfig）"}

    if threading.current_thread().ident in _subagent_threads:
        return {"ok": False, "error": "子 agent 不允许再派生子 agent"}

    if not tasks:
        return {"ok": False, "error": "任务列表不能为空"}

    for i, t in enumerate(tasks):
        if not t.get("task", "").strip():
            return {"ok": False, "error": f"第 {i+1} 个子任务描述不能为空"}

    if len(tasks) == 1:
        result = _run_subagent(tasks[0]["task"], workspace, tasks[0].get("context", ""))
        return {"ok": True, "results": [result]}

    futures = {}
    for i, t in enumerate(tasks):
        future = _executor.submit(
            _run_in_thread, t["task"], workspace, t.get("context", "")
        )
        futures[future] = i

    results = [None] * len(tasks)
    for future in as_completed(futures):
        idx = futures[future]
        try:
            results[idx] = future.result()
        except Exception as exc:
            results[idx] = {"ok": False, "error": f"子任务异常: {exc}"}

    return {"ok": True, "results": results}


def _run_in_thread(task: str, workspace: Path | None, context: str) -> dict:
    tid = threading.current_thread().ident
    _subagent_threads.add(tid)
    try:
        return _run_subagent(task, workspace, context)
    finally:
        _subagent_threads.discard(tid)


def _run_subagent(task: str, workspace: Path | None, context: str) -> dict:
    from chrysalis.agent_loop import AgentLoop
    from chrysalis.tools import generate_tools_schema

    child_llm = create_client(_session_config)

    child_progress = None
    if _progress is not None:
        def child_progress(msg: str) -> None:
            _progress(f"[子任务] {msg}")

    child_tools = generate_tools_schema(exclude={"spawn_subagent"})

    loop = AgentLoop(
        llm=child_llm,
        workspace=workspace or Path("."),
        max_turns=SUBAGENT_MAX_TURNS,
        progress=child_progress,
        use_function_calling=True,
        tools_schema=child_tools,
    )

    result = loop.run(task, session_context=context)

    if result.get("need_user"):
        return {"ok": False, "error": f"子任务需要用户输入：{result.get('question', '')}"}

    if result.get("ok"):
        return {"ok": True, "result": result["final"]}

    return {"ok": False, "error": result.get("final", "子任务失败")}
