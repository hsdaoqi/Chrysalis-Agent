"""对话消息渲染 widget。"""

from textual.app import ComposeResult
from textual.widgets import Static

from chrysalis.tui.theme import AGENT_COLOR, USER_COLOR


class MessageView(Static):
    """单条对话消息。"""

    DEFAULT_CSS = """
    MessageView {
        padding: 0 1;
        margin: 0 0 1 0;
    }
    MessageView .msg-role {
        width: auto;
        margin: 0 1 0 0;
    }
    MessageView .msg-content {
        width: 1fr;
    }
    """

    def __init__(self, role: str, content: str) -> None:
        super().__init__()
        self.role = role
        self.content_text = content

    def compose(self) -> ComposeResult:
        if self.role == "user":
            color = USER_COLOR
            label = "You"
        else:
            color = AGENT_COLOR
            label = "Chrysalis"
        yield Static(f"[bold {color}][{label}][/]  {self.content_text}")
