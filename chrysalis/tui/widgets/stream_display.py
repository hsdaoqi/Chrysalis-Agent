"""实时流式输出 widget。"""

from textual.widgets import Static

from chrysalis.tui.theme import AGENT_COLOR


class StreamDisplay(Static):
    """实时显示 LLM 流式输出，完成后可转为静态消息。"""

    DEFAULT_CSS = """
    StreamDisplay {
        padding: 0 1;
        margin: 0 0 1 0;
    }
    """

    def __init__(self) -> None:
        super().__init__("")
        self._buffer = ""

    def append_chunk(self, chunk: str) -> None:
        self._buffer += chunk
        self.update(f"[bold {AGENT_COLOR}][Chrysalis][/]  {self._buffer}[dim]...[/]")

    def finalize(self, content: str | None = None) -> None:
        text = content if content is not None else self._buffer
        self.update(f"[bold {AGENT_COLOR}][Chrysalis][/]  {text}")

    @property
    def content_text(self) -> str:
        return self._buffer
