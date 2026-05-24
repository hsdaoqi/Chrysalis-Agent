"""sync AgentLoop <-> async TUI 桥接层。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from chrysalis.kernel import Kernel
from chrysalis.tui.events import (
    AgentDone,
    FileDiff,
    StatusChange,
    StreamChunk,
    StreamDone,
    ToolCallStarted,
    ToolCallCompleted,
)

if TYPE_CHECKING:
    from chrysalis.tui.app import ChrysalisApp

_FILE_MODIFY_TOOLS = {"file_write", "file_patch"}


class AgentBridge:
    """在后台线程运行 Kernel，通过事件投递到 TUI。"""

    def __init__(self, app: ChrysalisApp) -> None:
        self.app = app
        self.kernel = Kernel(progress=self._on_progress)
        self._stream_buffer = ""
        self._file_before: dict[str, str] = {}
        self._setup_callbacks()

    def _setup_callbacks(self) -> None:
        self.kernel.loop.on_stream_chunk = self._on_stream_chunk
        self.kernel.loop.on_tool_call = self._on_tool_call

    def run_task(self, task: str) -> None:
        """在后台线程中调用，阻塞直到任务完成。"""
        self._stream_buffer = ""
        self._post(StatusChange("thinking"))
        try:
            result = self.kernel.run(task)
        except Exception as exc:
            result = {"ok": False, "error": str(exc), "final": f"异常：{exc}"}
        self._flush_stream()
        self._post(AgentDone(result))
        self._post(StatusChange("idle"))

    def cancel_task(self) -> None:
        self.kernel.cancel()

    def _on_stream_chunk(self, chunk: str) -> None:
        self._stream_buffer += chunk
        self._post(StreamChunk(chunk))

    def _on_tool_call(self, tool: str, args: dict, observation: dict | None) -> None:
        if observation is None:
            self._flush_stream()
            self._capture_file_before(tool, args)
            self._post(StatusChange("executing", tool))
            self._post(ToolCallStarted(tool, args))
        else:
            self._post(ToolCallCompleted(tool, args, observation))
            self._emit_diff_if_needed(tool, args, observation)
            self._post(StatusChange("thinking"))

    def _on_progress(self, message: str) -> None:
        pass

    def _flush_stream(self) -> None:
        if self._stream_buffer:
            self._post(StreamDone(self._stream_buffer))
            self._stream_buffer = ""

    def _capture_file_before(self, tool: str, args: dict) -> None:
        if tool not in _FILE_MODIFY_TOOLS:
            return
        path_str = args.get("path", "")
        if not path_str:
            return
        workspace = self.kernel.config.workspace_dir
        target = Path(path_str)
        if not target.is_absolute():
            target = workspace / target
        try:
            self._file_before[path_str] = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self._file_before[path_str] = ""

    def _emit_diff_if_needed(self, tool: str, args: dict, obs: dict) -> None:
        if tool not in _FILE_MODIFY_TOOLS or not obs.get("ok"):
            return
        path_str = args.get("path", "")
        before = self._file_before.pop(path_str, "")
        resolved = obs.get("path", path_str)
        try:
            after = Path(resolved).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        if before != after:
            self._post(FileDiff(path_str, before, after))

    def _post(self, event) -> None:
        self.app.call_from_thread(self.app.post_message, event)

    @property
    def model_name(self) -> str:
        return self.kernel.active_model_name
