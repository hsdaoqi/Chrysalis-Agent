"""可折叠工具调用面板。"""

import json
import time

from textual.app import ComposeResult
from textual.widgets import Collapsible, Static

from chrysalis.tui.theme import DIM_COLOR, ERROR_COLOR, SUCCESS_COLOR, TOOL_COLOR


class ToolPanel(Static):
    """显示一次工具调用的面板，可折叠展开查看详情。"""

    DEFAULT_CSS = """
    ToolPanel {
        margin: 0 0 1 0;
        padding: 0 1;
    }
    ToolPanel .tool-detail {
        padding: 0 2;
        color: #a9b1d6;
    }
    """

    def __init__(self, tool: str, args: dict) -> None:
        super().__init__()
        self.tool_name = tool
        self.tool_args = args
        self.started_at = time.time()
        self.observation: dict | None = None

    def compose(self) -> ComposeResult:
        args_brief = self._brief_args()
        title = f"[{TOOL_COLOR}]> {self.tool_name}[/]({args_brief})  [{DIM_COLOR}]running...[/]"
        with Collapsible(title=title, collapsed=True, id="tool-collapse"):
            yield Static(self._format_args(), classes="tool-detail")

    def complete(self, observation: dict) -> None:
        self.observation = observation
        elapsed = time.time() - self.started_at
        ok = observation.get("ok", False)
        status_icon = f"[{SUCCESS_COLOR}]✓[/]" if ok else f"[{ERROR_COLOR}]✗[/]"
        args_brief = self._brief_args()
        title = f"[{TOOL_COLOR}]> {self.tool_name}[/]({args_brief})  {status_icon} [{DIM_COLOR}]{elapsed:.1f}s[/]"
        collapsible = self.query_one("#tool-collapse", Collapsible)
        collapsible.title = title
        detail = self.query_one(".tool-detail", Static)
        detail.update(self._format_result(observation))

    def _brief_args(self) -> str:
        parts = []
        for k, v in self.tool_args.items():
            s = str(v)
            if len(s) > 30:
                s = s[:27] + "..."
            parts.append(f'{k}="{s}"')
        return ", ".join(parts[:2])

    def _format_args(self) -> str:
        return json.dumps(self.tool_args, ensure_ascii=False, indent=2)

    def _format_result(self, obs: dict) -> str:
        lines = [self._format_args(), "---"]
        if obs.get("error"):
            lines.append(f"[{ERROR_COLOR}]Error: {obs['error']}[/]")
        elif obs.get("content"):
            content = str(obs["content"])
            if len(content) > 500:
                content = content[:500] + "\n... (truncated)"
            lines.append(content)
        elif obs.get("stdout"):
            stdout = str(obs["stdout"])
            if len(stdout) > 500:
                stdout = stdout[:500] + "\n... (truncated)"
            lines.append(stdout)
        else:
            lines.append(json.dumps(obs, ensure_ascii=False, indent=2)[:500])
        return "\n".join(lines)
