import chrysalis.tools as tools
from chrysalis.tools import ask_user, code_run, file_list, file_patch, file_read, file_write, run_tool, shell_run


def test_file_tools_use_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    result = file_write("note.txt", "hello", workspace)
    assert result["ok"] is True
    assert file_read("note.txt", workspace)["content"] == "hello"
    names = {entry["name"] for entry in file_list(".", workspace)["entries"]}
    assert "note.txt" in names


def test_file_write_supports_append_and_prepend(tmp_path):
    workspace = tmp_path / "workspace"
    file_write("note.txt", "middle", workspace)
    file_write("note.txt", "start-", workspace, mode="prepend")
    file_write("note.txt", "-end", workspace, mode="append")

    assert file_read("note.txt", workspace)["content"] == "start-middle-end"


def test_file_tools_can_read_project_memory():
    result = file_read("memory/global_mem_insight.txt")
    assert result["ok"] is True
    assert "Global Memory Insight" in result["content"]


def test_file_tools_allow_workspace_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    result = file_read("../outside.txt", workspace)

    assert result["ok"] is True
    assert result["content"] == "outside"


def test_file_read_can_return_line_window(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    result = file_read("note.txt", workspace, start=2, count=2, show_linenos=True)

    assert result["ok"] is True
    assert result["content"] == "2|two\n3|three"
    assert result["total_lines"] == 4


def test_file_read_can_find_keyword(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    result = run_tool("file_read", {"path": "note.txt", "keyword": "BET"}, workspace)

    assert result["ok"] is True
    assert result["start"] == 2
    assert result["content"].startswith("2|beta")


def test_file_patch_replaces_unique_block(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    result = file_patch("note.txt", "beta", "BETA", workspace)

    assert result["ok"] is True
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"


def test_file_patch_rejects_non_unique_block(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("same\nsame\n", encoding="utf-8")

    result = file_patch("note.txt", "same", "changed", workspace)

    assert result["ok"] is False
    assert "不唯一" in result["error"]


def test_run_tool_returns_error_observation(tmp_path):
    result = run_tool("file_list", {"path": "."}, tmp_path / "missing")

    assert result["ok"] is False
    assert "missing" in result["error"]


def test_code_run_parses_json_output(tmp_path):
    result = code_run('print({"ok": True, "answer": 3})', tmp_path)
    assert result["ok"] is True
    assert result["answer"] == 3

    result = code_run('import json\nprint(json.dumps({"answer": 3}))', tmp_path)
    assert result["ok"] is True
    assert result["answer"] == 3


def test_code_run_keeps_plain_stdout(tmp_path):
    result = code_run('print("hello")', tmp_path)

    assert result["ok"] is True
    assert result["stdout"] == "hello"


def test_code_run_parses_python_dict_output(tmp_path):
    result = code_run('print({"answer": 3})', tmp_path)

    assert result["ok"] is True
    assert result["answer"] == 3


def test_code_run_blocks_dangerous_code(tmp_path):
    result = code_run("import subprocess", tmp_path)
    assert result["ok"] is False
    assert "暂不允许" in result["error"]


def test_shell_run_executes_safe_command(tmp_path):
    result = shell_run("Write-Output hello", tmp_path) if __import__("sys").platform.startswith("win") else shell_run("printf hello", tmp_path)

    assert result["ok"] is True
    assert result["stdout"] == "hello"


def test_shell_run_blocks_dangerous_command(tmp_path):
    result = shell_run("Remove-Item note.txt", tmp_path)

    assert result["ok"] is False
    assert "安全策略" in result["error"]


def test_ask_user_returns_interrupt_shape():
    result = ask_user("继续吗？", ["继续", "停止"])

    assert result["need_user"] is True
    assert result["question"] == "继续吗？"
    assert result["candidates"] == ["继续", "停止"]


def test_run_tool_routes_web_scan(monkeypatch, tmp_path):
    class FakeBrowser:
        def scan(self, **kwargs):
            return {"ok": True, "called": "scan", "kwargs": kwargs}

    monkeypatch.setattr(tools, "_BROWSER", FakeBrowser())

    result = run_tool("web_scan", {"url": "https://example.test", "text_only": True}, tmp_path)

    assert result["ok"] is True
    assert result["called"] == "scan"
    assert result["kwargs"]["url"] == "https://example.test"
    assert result["kwargs"]["text_only"] is True


def test_run_tool_routes_web_execute_js(monkeypatch, tmp_path):
    class FakeBrowser:
        def execute_js(self, **kwargs):
            return {"ok": True, "called": "js", "kwargs": kwargs}

    monkeypatch.setattr(tools, "_BROWSER", FakeBrowser())

    result = run_tool("web_execute_js", {"script": "() => document.title", "timeout_ms": 1234}, tmp_path)

    assert result["ok"] is True
    assert result["called"] == "js"
    assert result["kwargs"]["script"] == "() => document.title"
    assert result["kwargs"]["timeout"] == 1234
