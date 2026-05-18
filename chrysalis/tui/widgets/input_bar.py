"""底部输入框 widget。"""

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Input, Static


class TaskSubmitted(Message):
    """用户提交了一个任务。"""

    def __init__(self, task: str) -> None:
        super().__init__()
        self.task = task


class InputBar(Static):
    """底部输入区域。"""

    DEFAULT_CSS = """
    InputBar {
        dock: bottom;
        height: 3;
        padding: 0 1;
    }
    InputBar > Horizontal {
        height: 3;
    }
    InputBar #prompt-label {
        width: 12;
        height: 3;
        content-align: left middle;
        color: #9ece6a;
    }
    InputBar #task-input {
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static("chrysalis> ", id="prompt-label")
            yield Input(placeholder="输入任务...", id="task-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value:
            event.input.clear()
            self.post_message(TaskSubmitted(value))

    def set_enabled(self, enabled: bool) -> None:
        inp = self.query_one("#task-input", Input)
        inp.disabled = not enabled
        if enabled:
            inp.focus()
