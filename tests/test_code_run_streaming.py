"""验证 code_run 工具的流式输出（on_stream 回调）。"""

from pathlib import Path

import chrysalis.tools  # noqa: F401  确保工具注册
from chrysalis.tools.code_tools import code_run
from chrysalis.tools.registry import run_tool


def test_code_run_streams_lines(tmp_path: Path) -> None:
    chunks: list[str] = []
    script = "\n".join(
        f"print('line-{i}')" for i in range(5)
    )
    result = code_run(
        {"script": script, "type": "python", "timeout": 30},
        workspace=tmp_path,
        on_stream=chunks.append,
    )

    assert result["ok"] is True
    # on_stream 被多次调用（逐行）。
    assert len(chunks) >= 5
    # 拼起来等于最终 stdout（去掉尾部空白后比较各行）。
    streamed = "".join(chunks)
    for i in range(5):
        assert f"line-{i}" in streamed
        assert f"line-{i}" in result["stdout"]


def test_code_run_timeout_still_works(tmp_path: Path) -> None:
    result = code_run(
        {
            "script": "import time\ntime.sleep(10)\nprint('done')",
            "type": "python",
            "timeout": 1,
        },
        workspace=tmp_path,
        on_stream=lambda chunk: None,
    )
    assert result["ok"] is False
    assert "超时" in result["error"]


def test_code_run_without_on_stream_is_unchanged(tmp_path: Path) -> None:
    result = code_run(
        {"script": "print('hello')", "type": "python", "timeout": 30},
        workspace=tmp_path,
    )
    assert result["ok"] is True
    assert "hello" in result["stdout"]


def test_run_tool_passes_on_stream_to_code_run(tmp_path: Path) -> None:
    chunks: list[str] = []
    result = run_tool(
        "code_run",
        {"script": "print('a')\nprint('b')", "type": "python", "timeout": 30},
        tmp_path,
        on_stream=chunks.append,
    )
    assert result["ok"] is True
    assert len(chunks) >= 2
    assert "a" in "".join(chunks)


def test_run_tool_without_on_stream_still_works(tmp_path: Path) -> None:
    result = run_tool(
        "code_run",
        {"script": "print('ok')", "type": "python", "timeout": 30},
        tmp_path,
    )
    assert result["ok"] is True
    assert "ok" in result["stdout"]
