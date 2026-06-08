"""代码执行工具：code_run（支持 Python 和 shell）。"""

import ast
import json
import locale
import os
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from configs.config import PROJECT_ROOT, project_path
from chrysalis.tools.registry import tool
from chrysalis.tools.safety import DANGEROUS_CODE_PATTERNS, blocked_shell_pattern, safe_path


@tool("code_run", "代码执行器，支持 Python 和 shell。多次调用可用 script 参数", params={
    "script": "代码内容",
    "type": "代码类型(python|powershell|bash)，默认python",
    "timeout": "超时秒数(默认30)",
    "cwd": "工作目录(可选)",
})
def code_run(args: dict, workspace: Path | None = None, on_stream: "Callable[[str], None] | None" = None) -> dict:
    code = args.get("script", args.get("code", ""))
    code_type = args.get("type", "python").strip().lower()
    timeout = int(args.get("timeout", 30))
    cwd = args.get("cwd")

    if code_type == "python":
        return _run_python(code, timeout, cwd, workspace, on_stream)
    else:
        return _run_shell(code, code_type, timeout, cwd, workspace, on_stream)


def _stream_process(
    cmd: list[str],
    cwd: str,
    timeout: int,
    env: dict | None,
    on_stream: "Callable[[str], None] | None",
) -> tuple[str, int]:
    """运行子进程，逐行读取 stdout(已合并 stderr)，实时通过 on_stream 回调吐行。

    返回 (合并后的完整输出, 退出码)。超时抛出 subprocess.TimeoutExpired。
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    chunks: list[str] = []

    def _pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            chunks.append(line)
            if on_stream:
                try:
                    on_stream(line)
                except Exception:
                    pass

    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        reader.join(timeout=1)
        raise
    reader.join()
    return "".join(chunks), proc.returncode


def _run_python(code: str, timeout: int, cwd: str | None, workspace: Path | None, on_stream: "Callable[[str], None] | None" = None) -> dict:
    for pattern in DANGEROUS_CODE_PATTERNS:
        if pattern in code:
            return {"ok": False, "error": f"代码包含暂不允许的片段: {pattern}"}

    base = safe_path(cwd, workspace) if cwd else (workspace or project_path("workspace"))
    base.mkdir(parents=True, exist_ok=True)

    header_path = PROJECT_ROOT / "assets" / "code_run_header.py"
    if header_path.exists():
        header = header_path.read_text(encoding="utf-8") + "\n"
    else:
        header = (
            "import sys\nimport json\nfrom pathlib import Path\n"
            f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
            f"sys.path.insert(0, {str(PROJECT_ROOT / 'memory')!r})\n"
        )

    prelude = (
        header
        + f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
        f"sys.path.insert(0, {str(PROJECT_ROOT / 'memory')!r})\n"
        f"PROJECT_ROOT = Path({str(PROJECT_ROOT)!r})\n"
        f"WORKSPACE = Path({str(base)!r})\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        script = Path(f.name)
        f.write(prelude + "\n" + code)

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        output, returncode = _stream_process(
            [sys.executable, "-X", "utf8", "-u", str(script)],
            cwd=str(base), timeout=timeout, env=env, on_stream=on_stream,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"代码执行超时: {timeout} 秒"}
    finally:
        try:
            script.unlink(missing_ok=True)
        except OSError:
            pass

    stdout = output.strip()
    if returncode != 0:
        return {"ok": False, "error": stdout or f"退出码: {returncode}"}

    parsed = _parse_last_json_line(stdout)
    if parsed is not None:
        parsed.setdefault("ok", True)
        return parsed
    return {"ok": True, "stdout": stdout}


def _run_shell(command: str, shell_type: str, timeout: int, cwd: str | None, workspace: Path | None, on_stream: "Callable[[str], None] | None" = None) -> dict:
    if not command:
        return {"ok": False, "error": "command 不能为空"}
    blocked = blocked_shell_pattern(command)
    if blocked:
        return {"ok": False, "error": f"shell 命令被安全策略拦截: {blocked}"}

    base = safe_path(cwd, workspace) if cwd else (workspace or project_path("workspace"))
    base.mkdir(parents=True, exist_ok=True)

    if shell_type == "bash":
        cmd = ["bash", "-lc", command]
    else:
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]

    try:
        output, returncode = _stream_process(
            cmd, cwd=str(base), timeout=timeout, env=None, on_stream=on_stream,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"shell 命令执行超时: {timeout} 秒"}

    return {
        "ok": returncode == 0,
        "exit_code": returncode,
        "stdout": output.strip()[:20_000],
        "stderr": "",
    }


def _decode_process_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    encodings = ["utf-8", "gbk", "cp936", locale.getpreferredencoding(False), "mbcs"]
    seen: set[str] = set()
    for encoding in encodings:
        if not encoding:
            continue
        normalized = encoding.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            return value.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return value.decode("utf-8", errors="replace")


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
