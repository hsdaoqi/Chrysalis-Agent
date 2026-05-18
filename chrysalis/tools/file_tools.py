"""文件操作工具：file_read, file_write, file_patch。"""

from pathlib import Path

from chrysalis.tools.registry import tool
from chrysalis.tools.safety import safe_path, optional_int, as_bool, SECRET_NAMES


@tool("file_read", "读取文件内容，可按行号或关键词定位", params={
    "path": "文件路径",
    "start": "起始行号(默认1)",
    "count": "读取行数(默认200)",
    "keyword": "搜索关键词(可选)",
    "show_linenos": "显示行号(默认true)",
})
def file_read(args: dict, workspace: Path | None = None) -> dict:
    path = args["path"]
    start = int(args.get("start", 1))
    count = optional_int(args.get("count", 200))
    keyword = args.get("keyword")
    show_linenos = as_bool(args.get("show_linenos", True))

    target = safe_path(path, workspace)
    text = target.read_text(encoding="utf-8")

    if count is None and keyword is None and start <= 1 and not show_linenos:
        return {"ok": True, "path": str(target), "content": text}

    lines = text.splitlines()
    total_lines = len(lines)
    start_index = max(start, 1) - 1

    if keyword:
        needle = keyword.lower()
        for index in range(start_index, total_lines):
            if needle in lines[index].lower():
                start_index = index
                break
        else:
            return {"ok": False, "path": str(target),
                    "error": f"从第 {start} 行之后没有找到关键词: {keyword}", "total_lines": total_lines}

    window_count = total_lines if count is None else max(count, 0)
    selected = lines[start_index:start_index + window_count]
    if show_linenos:
        content = "\n".join(f"{n}|{line}" for n, line in enumerate(selected, start_index + 1))
    else:
        content = "\n".join(selected)
    return {"ok": True, "path": str(target), "content": content,
            "start": start_index + 1, "lines_returned": len(selected),
            "total_lines": total_lines, "partial": len(selected) < total_lines}


@tool("file_write", "写入文本文件", params={
    "path": "文件路径",
    "content": "写入内容",
    "mode": "写入模式(overwrite|append|prepend)",
})
def file_write(args: dict, workspace: Path | None = None) -> dict:
    path = args["path"]
    content = args.get("content", "")
    mode = args.get("mode", "overwrite")

    target = safe_path(path, workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "overwrite":
        target.write_text(content, encoding="utf-8")
    elif mode == "append":
        with target.open("a", encoding="utf-8") as f:
            f.write(content)
    elif mode == "prepend":
        old = target.read_text(encoding="utf-8") if target.exists() else ""
        target.write_text(content + old, encoding="utf-8")
    else:
        return {"ok": False, "error": f"不支持的写入模式: {mode}"}
    return {"ok": True, "path": str(target), "mode": mode}


@tool("file_patch", "替换文件中唯一匹配的文本块，修改前必须先读取确认", params={
    "path": "文件路径",
    "old_content": "要替换的原文(必须唯一)",
    "new_content": "替换后的新文本",
})
def file_patch(args: dict, workspace: Path | None = None) -> dict:
    path = args["path"]
    old_content = args.get("old_content", "")
    new_content = args.get("new_content", "")

    if not old_content:
        return {"ok": False, "error": "old_content 不能为空"}
    target = safe_path(path, workspace)
    text = target.read_text(encoding="utf-8")
    matches = text.count(old_content)
    if matches == 0:
        return {"ok": False, "path": str(target), "error": "没有找到 old_content"}
    if matches > 1:
        return {"ok": False, "path": str(target), "error": f"old_content 不唯一，共匹配 {matches} 处"}
    target.write_text(text.replace(old_content, new_content, 1), encoding="utf-8")
    return {"ok": True, "path": str(target), "replacements": 1}
