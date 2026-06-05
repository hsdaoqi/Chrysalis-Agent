"""Agent 控制工具：ask_user, update_working_checkpoint, start_long_term_update。"""

from pathlib import Path

from chrysalis.gateway.bootstrap import format_launch_summary, normalize_gateway_platforms, start_gateway_process
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


@tool(
    "gateway_connect",
    "启动 Chrysalis 消息网关到新窗口，准备 QQ、qq-personal/OneBot、个人微信或飞书接入。",
    params={
        "platform": "wechat, qq, qq-personal/onebot, feishu/lark, or comma-separated platforms (default wechat)",
        "shared_groups": "true/false, group sessions per user or shared",
        "hidden": "true/false, launch without a visible console window",
    },
)
def gateway_connect(args: dict, workspace: Path | None = None) -> dict:
    del workspace
    platform_arg = str(args.get("platform") or "wechat").strip()
    raw_platforms = [part.strip() for part in platform_arg.replace(";", ",").split(",") if part.strip()]
    try:
        platforms = normalize_gateway_platforms(raw_platforms or ["wechat"])
    except SystemExit as exc:
        return {"ok": False, "error": str(exc)}

    shared_groups = str(args.get("shared_groups") or "").strip().lower() in {"1", "true", "yes", "on"}
    hidden = str(args.get("hidden") or "").strip().lower() in {"1", "true", "yes", "on"}
    try:
        result = start_gateway_process(platforms, shared_groups=shared_groups, visible=not hidden)
    except SystemExit as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "platforms": result.platforms,
        "pid": result.pid,
        "command": result.command,
        "visible": result.visible,
        "log_file": str(result.log_file) if result.log_file else "",
        "message": format_launch_summary(result),
    }


@tool("todo_write", "Maintain a simple TODO list that breaks the current task into concrete steps.", params={
    "goal": "The overall task goal.",
    "todos": "TODO items as a string list or object list. Each object may include id, title, status, and note.",
    "items": "Alias for todos.",
    "action": "Operation(set|replace|append|update|complete|clear|reset|reorder).",
})
def todo_write(args: dict, workspace: Path | None = None) -> dict:
    action = str(args.get("action", "set")).strip().lower()
    return {
        "ok": True,
        "_todo": True,
        "message": "TODO list updated",
        "todo_action": action,
        "goal": str(args.get("goal", "")).strip(),
        "todos": args.get("todos") or args.get("items") or args.get("steps") or [],
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
