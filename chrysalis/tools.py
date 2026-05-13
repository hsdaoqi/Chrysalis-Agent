"""Chrysalis 的最小 GA 风格原子工具。"""

import ast
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from chrysalis.browser import BrowserController
from chrysalis.config import PROJECT_ROOT, project_path


SECRET_NAMES = {".env", "id_rsa", "id_ed25519"}
PROJECT_SCOPED_NAMES = {"chrysalis", "data", "memory", "skills", "tests", "workspace", "README.md", "pyproject.toml"}
DANGEROUS_CODE_PATTERNS = (
    "subprocess",
    "os.system",
    "os.popen",
    "shutil.rmtree",
    "socket",
    "ctypes",
)
DANGEROUS_SHELL_PATTERNS = (
    r"\brm\b",
    r"\bdel\b",
    r"\brd\b",
    r"\brmdir\b",
    r"\bremove-item\b",
    r"\bformat\b",
    r"\bshutdown\b",
    r"\brestart-computer\b",
    r"\bstop-process\b",
    r"\btaskkill\b",
    r"\bkill\b",
    r"\bgit\s+reset\s+--hard\b",
)
_BROWSER = BrowserController()


def _safe_path(path: str | Path, workspace: Path | None = None) -> Path:
    raw = Path(path).expanduser()
    if raw.is_absolute():
        resolved = raw.resolve()
    else:
        parts = raw.parts
        if parts and parts[0] in PROJECT_SCOPED_NAMES:
            base = PROJECT_ROOT
        else:
            base = workspace or project_path("workspace")
        resolved = (base / raw).resolve()
    if resolved.name in SECRET_NAMES or resolved.suffix in {".pem", ".key"}:
        raise PermissionError(f"拒绝访问密钥文件: {resolved}")
    return resolved


def file_read(
    path: str,
    workspace: Path | None = None,
    start: int = 1,
    count: int | None = None,
    keyword: str | None = None,
    show_linenos: bool = False,
) -> dict:
    target = _safe_path(path, workspace)
    text = target.read_text(encoding="utf-8")
    if count is None and keyword is None and start <= 1 and not show_linenos:
        return {"ok": True, "path": str(target), "content": text}

    lines = text.splitlines()
    total_lines = len(lines)
    start_index = max(int(start), 1) - 1
    if keyword:
        needle = keyword.lower()
        for index in range(start_index, total_lines):
            if needle in lines[index].lower():
                start_index = index
                break
        else:
            return {
                "ok": False,
                "path": str(target),
                "error": f"从第 {start} 行之后没有找到关键词: {keyword}",
                "total_lines": total_lines,
            }

    window_count = total_lines if count is None else max(int(count), 0)
    selected = lines[start_index:start_index + window_count]
    if show_linenos:
        content = "\n".join(f"{line_no}|{line}" for line_no, line in enumerate(selected, start_index + 1))
    else:
        content = "\n".join(selected)
    return {
        "ok": True,
        "path": str(target),
        "content": content,
        "start": start_index + 1,
        "lines_returned": len(selected),
        "total_lines": total_lines,
        "partial": len(selected) < total_lines,
    }


def file_write(path: str, content: str, workspace: Path | None = None, mode: str = "overwrite") -> dict:
    target = _safe_path(path, workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "overwrite":
        target.write_text(content, encoding="utf-8")
    elif mode == "append":
        with target.open("a", encoding="utf-8") as handle:
            handle.write(content)
    elif mode == "prepend":
        old = target.read_text(encoding="utf-8") if target.exists() else ""
        target.write_text(content + old, encoding="utf-8")
    else:
        return {"ok": False, "error": f"不支持的写入模式: {mode}"}
    return {"ok": True, "path": str(target), "mode": mode}


def file_patch(path: str, old_content: str, new_content: str, workspace: Path | None = None) -> dict:
    target = _safe_path(path, workspace)
    if not old_content:
        return {"ok": False, "path": str(target), "error": "old_content 不能为空"}
    text = target.read_text(encoding="utf-8")
    matches = text.count(old_content)
    if matches == 0:
        return {"ok": False, "path": str(target), "error": "没有找到 old_content"}
    if matches > 1:
        return {"ok": False, "path": str(target), "error": f"old_content 不唯一，共匹配 {matches} 处"}
    target.write_text(text.replace(old_content, new_content, 1), encoding="utf-8")
    return {"ok": True, "path": str(target), "replacements": 1}


def file_list(path: str = ".", workspace: Path | None = None) -> dict:
    target = _safe_path(path, workspace)
    entries = []
    for item in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        if item.name in SECRET_NAMES:
            continue
        entries.append({"name": item.name, "path": str(item), "type": "dir" if item.is_dir() else "file"})
    return {"ok": True, "path": str(target), "entries": entries}


def web_fetch(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=20) as response:
        body = response.read().decode("utf-8", errors="replace")
    return {"ok": True, "url": url, "status": response.status, "body": body[:200_000]}


def web_scan(
    url: str | None = None,
    tab_id: str | None = None,
    tabs_only: bool = False,
    text_only: bool = False,
    wait_ms: int = 1000,
) -> dict:
    """扫描浏览器页面，返回标签页、正文和可交互元素摘要。"""
    return _BROWSER.scan(
        url=url,
        tab_id=tab_id,
        tabs_only=tabs_only,
        text_only=text_only,
        wait_ms=wait_ms,
    )


def web_execute_js(script: str, tab_id: str | None = None, timeout_ms: int = 10_000) -> dict:
    """在当前真实浏览器标签页执行一段 JS。"""
    return _BROWSER.execute_js(script=script, tab_id=tab_id, timeout=timeout_ms)


def code_run(code: str, workspace: Path | None = None, timeout: int = 20) -> dict:
    """运行一段短 Python 代码，带最小拒绝列表和超时控制。"""
    for pattern in DANGEROUS_CODE_PATTERNS:
        if pattern in code:
            return {"ok": False, "error": f"代码包含暂不允许的片段: {pattern}"}

    base = workspace or project_path("workspace")
    base.mkdir(parents=True, exist_ok=True)
    prelude = (
        "import sys\n"
        "import json\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
        f"PROJECT_ROOT = Path({str(PROJECT_ROOT)!r})\n"
        f"WORKSPACE = Path({str(base)!r})\n"
        "from chrysalis.tools import file_read, file_write, file_patch, file_list, web_fetch, web_scan, web_execute_js\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as handle:
        script = Path(handle.name)
        handle.write(prelude + "\n" + code)

    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(base),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"代码执行超时: {timeout} 秒"}
    finally:
        try:
            script.unlink(missing_ok=True)
        except OSError:
            pass

    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    if proc.returncode != 0:
        return {"ok": False, "error": stderr or stdout or f"退出码: {proc.returncode}"}

    parsed = _parse_last_json_line(stdout)
    if parsed is not None:
        parsed.setdefault("ok", True)
        return parsed
    return {"ok": True, "stdout": stdout}


def shell_run(
    command: str,
    workspace: Path | None = None,
    timeout: int = 30,
    cwd: str | None = None,
) -> dict:
    """运行一条受控 shell 命令，默认工作目录是 workspace。"""
    if not command.strip():
        return {"ok": False, "error": "command 不能为空"}
    blocked = _blocked_shell_pattern(command)
    if blocked:
        return {"ok": False, "error": f"shell 命令被安全策略拦截: {blocked}"}

    base = _safe_path(cwd, workspace) if cwd else (workspace or project_path("workspace"))
    base.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        cmd = ["bash", "-lc", command]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(base),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"shell 命令执行超时: {timeout} 秒", "command": command}

    return {
        "ok": proc.returncode == 0,
        "command": command,
        "cwd": str(base),
        "exit_code": proc.returncode,
        "stdout": proc.stdout.strip()[:20_000],
        "stderr": proc.stderr.strip()[:20_000],
    }


def ask_user(question: str, candidates: list[str] | None = None) -> dict:
    """把任务中断为一个明确的人类输入请求。"""
    return {
        "ok": False,
        "need_user": True,
        "question": question,
        "candidates": candidates or [],
        "message": "需要用户输入后才能继续",
    }


def run_tool(name: str, args: dict, workspace: Path | None = None) -> dict:
    try:
        if name == "file_read":
            if not any(key in args for key in ("start", "count", "keyword", "show_linenos")):
                return file_read(args["path"], workspace)
            return file_read(
                args["path"],
                workspace,
                start=int(args.get("start", 1)),
                count=_optional_int(args.get("count", 200)),
                keyword=args.get("keyword"),
                show_linenos=_as_bool(args.get("show_linenos", True)),
            )
        if name == "file_write":
            return file_write(args["path"], args.get("content", ""), workspace, args.get("mode", "overwrite"))
        if name == "file_patch":
            return file_patch(args["path"], args.get("old_content", ""), args.get("new_content", ""), workspace)
        if name == "file_list":
            return file_list(args.get("path", "."), workspace)
        if name == "web_fetch":
            return web_fetch(args["url"])
        if name == "web_scan":
            return web_scan(
                url=args.get("url"),
                tab_id=args.get("tab_id"),
                tabs_only=_as_bool(args.get("tabs_only", False)),
                text_only=_as_bool(args.get("text_only", False)),
                wait_ms=int(args.get("wait_ms", 1000)),
            )
        if name == "web_execute_js":
            return web_execute_js(
                args.get("script", ""),
                tab_id=args.get("tab_id"),
                timeout_ms=int(args.get("timeout_ms", args.get("timeout", 10_000))),
            )
        if name == "code_run":
            return code_run(args.get("code", ""), workspace, int(args.get("timeout", 20)))
        if name == "shell_run":
            return shell_run(
                args.get("command", ""),
                workspace,
                int(args.get("timeout", 30)),
                args.get("cwd"),
            )
        if name == "ask_user":
            return ask_user(args.get("question", ""), args.get("candidates") or [])
        return {"ok": False, "error": f"未知工具: {name}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


TOOL_PROMPT = f"""可用工具：
- file_list(path=".") -> 列出文件。普通相对路径默认位于 {PROJECT_ROOT / "workspace"}。
- file_read(path, start=1, count=200, keyword=null, show_linenos=true) -> 读取文本窗口，可按行号或关键词定位。
- file_write(path, content, mode="overwrite|append|prepend") -> 写入文本文件。
- file_patch(path, old_content, new_content) -> 替换唯一匹配的文本块。修改前必须先读取确认。
- web_fetch(url) -> 获取网页文本。
- web_scan(url=null, tab_id=null, tabs_only=false, text_only=false, wait_ms=1000) -> 用本机真实 Chrome/Edge 打开或扫描网页，返回标签页、正文、链接、表单和按钮摘要。不能打开真实浏览器时必须承认失败。
- web_execute_js(script, tab_id=null, timeout_ms=10000) -> 在当前真实浏览器标签页执行 JS；用于点击、输入、读取动态页面状态。
- code_run(code, timeout=20) -> 运行一段短 Python 代码，最好 print 一个 JSON 对象。
- shell_run(command, timeout=30, cwd=null) -> 运行一条受控 shell 命令，默认在 workspace 执行。
- ask_user(question, candidates=[]) -> 遇到阻塞、需要选择或有风险决策时询问用户。
- update_working_checkpoint(key_info, related_sop="") -> 更新当前任务的短期工作记忆。
- start_long_term_update(reason="") -> 当前任务有可沉淀经验时启动长期记忆更新流程。

读取项目资料时可直接使用 memory/、data/、skills/、chrysalis/、tests/、workspace/、README.md、pyproject.toml。
只能返回 JSON：
调用已有技能：
{{"skill": "技能名", "thought": "为什么这个技能适合"}}
调用原子工具：
{{"tool": "file_list", "args": {{"path": "."}}, "thought": "简短原因"}}
给最终回答：
{{"final": "给用户的回答"}}
"""


def dumps_observation(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _parse_last_json_line(text: str) -> dict | None:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            try:
                value = ast.literal_eval(line)
            except (ValueError, SyntaxError):
                continue
        return value if isinstance(value, dict) else {"value": value}
    return None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _blocked_shell_pattern(command: str) -> str | None:
    lowered = command.lower()
    for pattern in DANGEROUS_SHELL_PATTERNS:
        if re.search(pattern, lowered):
            return pattern
    return None
