"""顶部状态栏 widget。"""

import time

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from chrysalis.tui.theme import AGENT_COLOR, DIM_COLOR


class StatusBar(Static):
    """显示模型、轮次、耗时等状态信息。"""

    DEFAULT_CSS = """
    StatusBar {
        dock: top;
        height: 1;
        background: $surface;
        padding: 0 1;
    }
    StatusBar > Horizontal {
        height: 1;
    }
    StatusBar .status-item {
        width: auto;
        margin: 0 2 0 0;
    }
    """

    def __init__(self, model: str = "") -> None:
        super().__init__()
        self._model = model
        self._status = "idle"
        self._turn = 0
        self._started: float | None = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static(f"[bold {AGENT_COLOR}]Chrysalis[/]", classes="status-item")
            yield Static(f"[{DIM_COLOR}]{self._model}[/]", id="model-info", classes="status-item")
            yield Static(f"[{DIM_COLOR}]idle[/]", id="status-text", classes="status-item")

    def set_status(self, status: str, detail: str = "") -> None:
        self._status = status
        if status == "thinking" and self._started is None:
            self._started = time.time()
        elif status == "idle":
            self._started = None
        text = status
        if detail:
            text += f" ({detail})"
        self.query_one("#status-text", Static).update(f"[{DIM_COLOR}]{text}[/]")

    def set_turn(self, turn: int) -> None:
        self._turn = turn

    def set_model(self, model: str) -> None:
        self._model = model
        self.query_one("#model-info", Static).update(f"[{DIM_COLOR}]{model}[/]")
