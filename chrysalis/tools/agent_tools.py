"""Agent 控制工具：ask_user, update_working_checkpoint, start_long_term_update。"""

from pathlib import Path

from chrysalis.tools.registry import tool


@tool("ask_user", "遇到阻塞或需要用户决策时询问用户", params={
    "question": "问题内容",
    "candidates": "可选的快捷选项列表",
})
def ask_user(args: dict, workspace: Path | None = None) -> dict:
    return {
        "ok": False,
        "need_user": True,
        "question": args.get("question", ""),
        "candidates": args.get("candidates") or [],
        "message": "需要用户输入后才能继续",
    }


@tool("update_working_checkpoint", "更新当前任务的短期工作记忆", params={
    "key_info": "当前任务的关键信息和进度",
    "related_sop": "相关 SOP 文件名(可选)",
})
def update_working_checkpoint(args: dict, workspace: Path | None = None) -> dict:
    return {"ok": True, "_checkpoint": True, "key_info": args.get("key_info", ""), "related_sop": args.get("related_sop", "")}


@tool("start_long_term_update", "当前任务有可沉淀经验时启动长期记忆更新", params={
    "reason": "为什么值得记忆",
})
def start_long_term_update(args: dict, workspace: Path | None = None) -> dict:
    return {"ok": True, "_long_term": True, "reason": args.get("reason", "")}


@tool("todo_write", "维护当前任务的 TODO 列表，用于拆解、更新、完成和重排子步骤", params={
    "todos": "TODO 列表，可以是字符串列表或对象列表",
    "goal": "当前任务目标",
    "action": "操作(set|replace|append|update|complete|clear|reset|reorder)",
})
def todo_write(args: dict, workspace: Path | None = None) -> dict:
    action = str(args.get("action", "set")).strip().lower()
    goal = str(args.get("goal", "")).strip()
    todos = args.get("todos") or args.get("items") or []
    return {
        "ok": True,
        "_todo": True,
        "message": "TODO list updated",
        "todo_action": action,
        "goal": goal,
        "todos": todos,
    }


@tool("spawn_subagent", "派生子 agent 执行子任务（独立上下文，并行执行）。阻塞直到全部完成，返回所有结果", params={
    "tasks": '子任务列表 [{"task": "描述", "context": "可选上下文"}, ...]',
    "task": "单个子任务描述（与 tasks 二选一）",
    "context": "单任务时的额外上下文（可选）",
})
def spawn_subagent(args: dict, workspace: Path | None = None) -> dict:
    from chrysalis.subagent import run_tasks
    tasks = args.get("tasks")
    if not tasks:
        task = args.get("task", "")
        if not task:
            return {"ok": False, "error": "需要提供 task 或 tasks 参数"}
        tasks = [{"task": task, "context": args.get("context", "")}]
    return run_tasks(tasks, workspace)
