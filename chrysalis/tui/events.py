"""TUI custom events for cross-thread communication."""

from textual.message import Message


class StreamChunk(Message):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class StreamDone(Message):
    def __init__(self, content: str) -> None:
        super().__init__()
        self.content = content


class ToolCallStarted(Message):
    def __init__(self, tool: str, args: dict) -> None:
        super().__init__()
        self.tool = tool
        self.args = args


class ToolCallCompleted(Message):
    def __init__(self, tool: str, args: dict, observation: dict) -> None:
        super().__init__()
        self.tool = tool
        self.args = args
        self.observation = observation


class FileDiff(Message):
    def __init__(self, path: str, before: str, after: str) -> None:
        super().__init__()
        self.path = path
        self.before = before
        self.after = after


class AgentDone(Message):
    def __init__(self, result: dict) -> None:
        super().__init__()
        self.result = result


class StatusChange(Message):
    def __init__(self, status: str, detail: str = "") -> None:
        super().__init__()
        self.status = status
        self.detail = detail


class VoiceResult(Message):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class WorkingChange(Message):
    def __init__(self, snapshot: dict) -> None:
        super().__init__()
        self.snapshot = snapshot
