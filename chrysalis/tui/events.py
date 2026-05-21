"""TUI 自定义事件，用于线程间通信。"""

from dataclasses import dataclass, field
from textual.message import Message


class StreamChunk(Message):
    """LLM 流式文本片段。"""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class StreamDone(Message):
    """LLM 流式输出完成。"""

    def __init__(self, content: str) -> None:
        super().__init__()
        self.content = content


class ToolCallStarted(Message):
    """工具开始执行。"""

    def __init__(self, tool: str, args: dict) -> None:
        super().__init__()
        self.tool = tool
        self.args = args


class ToolCallCompleted(Message):
    """工具执行完成。"""

    def __init__(self, tool: str, args: dict, observation: dict) -> None:
        super().__init__()
        self.tool = tool
        self.args = args
        self.observation = observation


class FileDiff(Message):
    """文件修改 diff。"""

    def __init__(self, path: str, before: str, after: str) -> None:
        super().__init__()
        self.path = path
        self.before = before
        self.after = after


class AgentDone(Message):
    """任务完成。"""

    def __init__(self, result: dict) -> None:
        super().__init__()
        self.result = result


class StatusChange(Message):
    """状态变更。"""

    def __init__(self, status: str, detail: str = "") -> None:
        super().__init__()
        self.status = status
        self.detail = detail


class VoiceResult(Message):
    """语音转写完成，携带识别文本。"""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text
