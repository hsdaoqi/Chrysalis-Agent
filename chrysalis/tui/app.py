"""Chrysalis TUI — Claude Code 风格，带轮次折叠面板。"""

from textual import work, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.widgets import Static, Input, Collapsible

from chrysalis.tui.bridge import AgentBridge
from chrysalis.tui.events import (
    AgentDone,
    FileDiff,
    StatusChange,
    StreamChunk,
    StreamDone,
    ToolCallCompleted,
    ToolCallStarted,
)


class TurnPanel(Static):
    """一轮 agent 操作的可折叠面板。"""

    DEFAULT_CSS = """
    TurnPanel {
        margin: 0 0 0 0;
        padding: 0;
    }
    TurnPanel > Collapsible {
        border-top: none;
        border-bottom: none;
        padding: 0 0 0 2;
        background: #0a0a0a;
    }
    TurnPanel > Collapsible > Contents {
        padding: 0 0 0 1;
    }
    TurnPanel .turn-detail {
        color: #585b70;
        padding: 0;
        margin: 0;
    }
    """

    def __init__(self, turn: int, tool: str, args_brief: str) -> None:
        super().__init__()
        self.turn_num = turn
        self.tool = tool
        self.args_brief = args_brief
        self._lines: list[str] = []
        self._status = "running"
        self._summary = ""
        self._mounted = False

    def compose(self) -> ComposeResult:
        title = self._make_title()
        with Collapsible(title=title, collapsed=True):
            yield Static("", classes="turn-detail")

    def on_mount(self) -> None:
        self._mounted = True
        if self._lines:
            self._refresh_detail()

    def add_line(self, line: str) -> None:
        self._lines.append(line)
        if self._mounted:
            self._refresh_detail()

    def set_complete(self, ok: bool, summary: str) -> None:
        self._status = "ok" if ok else "error"
        self._summary = summary
        if self._mounted:
            collapsible = self.query_one(Collapsible)
            collapsible.title = self._make_title()
            self._refresh_detail()

    def _make_title(self) -> str:
        if self._status == "running":
            return f"[#b4befe]Turn {self.turn_num}[/] [#585b70]│[/] [#89b4fa]⏵[/] [#cdd6f4]{self.tool}[/][#585b70]({self.args_brief})[/] [#585b70]…[/]"
        elif self._status == "ok":
            return f"[#b4befe]Turn {self.turn_num}[/] [#585b70]│[/] [#a6e3a1]✓[/] [#cdd6f4]{self.tool}[/] [#585b70]— {self._summary}[/]"
        else:
            return f"[#b4befe]Turn {self.turn_num}[/] [#585b70]│[/] [#f38ba8]✗[/] [#cdd6f4]{self.tool}[/] [#585b70]— {self._summary}[/]"

    def _refresh_detail(self) -> None:
        detail = self.query_one(".turn-detail", Static)
        detail.update("\n".join(self._lines) if self._lines else "[#585b70](empty)[/]")


class ChrysalisApp(App):

    TITLE = "Chrysalis"
    CSS = """
    Screen {
        background: #000000;
    }
    #scroll {
        height: 1fr;
        padding: 0 1;
        scrollbar-size: 1 1;
        scrollbar-color: #333333;
        scrollbar-background: #000000;
    }
    #scroll:focus {
        border: none;
    }
    #bottom {
        dock: bottom;
        height: 2;
        background: #000000;
        padding: 0 1;
    }
    #input-row {
        height: 1;
        layout: horizontal;
    }
    #prompt {
        width: 3;
        height: 1;
        padding: 0;
        color: #b4befe;
    }
    #input {
        background: #000000;
        border: none;
        height: 1;
        width: 1fr;
        padding: 0;
        color: #cdd6f4;
    }
    #input:focus {
        border: none;
    }
    #input.-disabled {
        opacity: 0.3;
    }
    #status {
        height: 1;
        padding: 0;
        color: #585b70;
    }
    .final-answer {
        margin: 1 0 0 0;
        padding: 0 0;
        color: #cdd6f4;
    }
    .stream-text {
        padding: 0;
        margin: 0;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", show=False),
        Binding("ctrl+l", "clear_screen", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.bridge = AgentBridge(self)
        self._streaming = False
        self._stream_widget: Static | None = None
        self._stream_buf = ""
        self._turn = 0
        self._current_panel: TurnPanel | None = None
        self._has_final = False

    def compose(self) -> ComposeResult:
        yield ScrollableContainer(id="scroll")
        with Vertical(id="bottom"):
            with Vertical(id="input-row"):
                yield Static("[#b4befe]>[/]", id="prompt")
                yield Input(id="input")
            yield Static("", id="status")

    def on_mount(self) -> None:
        self._out("[#b4befe bold]Chrysalis[/] [#585b70]v0.1 · autonomous agent[/]")
        self._out("[#585b70]Type a task, or press Ctrl+C to exit.[/]")
        self._out("")
        self._update_status("ready")
        self.query_one("#input", Input).focus()

    # ── Input ──

    def on_input_submitted(self, event: Input.Submitted) -> None:
        task = event.value.strip()
        if not task:
            return
        event.input.clear()
        self._out(f"[bold #cdd6f4]> {task}[/]")
        self._out("")
        self._set_input(False)
        self._turn = 0
        self._has_final = False
        self._current_panel = None
        self._run_agent(task)

    @work(thread=True)
    def _run_agent(self, task: str) -> None:
        self.bridge.run_task(task)

    # ── Stream (思考过程，放入当前轮面板) ──

    def on_stream_chunk(self, event: StreamChunk) -> None:
        if not self._streaming:
            self._streaming = True
            self._stream_buf = ""
            self._stream_widget = Static("", markup=True, classes="stream-text")
            self.query_one("#scroll").mount(self._stream_widget)
        self._stream_buf += event.text
        self._stream_widget.update(f"[#585b70]{self._stream_buf}[/][#b4befe]▎[/]")
        self._scroll()

    def on_stream_done(self, event: StreamDone) -> None:
        if self._streaming and self._stream_widget:
            self._stream_widget.remove()
            self._stream_widget = None
        self._streaming = False

    # ── Tool calls → 创建轮次面板 ──

    def on_tool_call_started(self, event: ToolCallStarted) -> None:
        self._turn += 1
        args_brief = self._fmt_args(event.args)

        if self._streaming and self._stream_widget:
            self._stream_widget.remove()
            self._stream_widget = None
            self._streaming = False

        panel = TurnPanel(self._turn, event.tool, args_brief)
        self._current_panel = panel
        self.query_one("#scroll").mount(panel)

        if self._stream_buf:
            panel.add_line(f"[#585b70]Thought: {self._stream_buf[:200]}[/]")
            self._stream_buf = ""

        panel.add_line(f"[#89b4fa]Args:[/] [#585b70]{self._fmt_args_full(event.args)}[/]")
        self._scroll()

    def on_tool_call_completed(self, event: ToolCallCompleted) -> None:
        obs = event.observation
        ok = obs.get("ok", False)
        summary = self._obs_summary(obs)

        if self._current_panel:
            if ok:
                content = self._obs_content(obs)
                if content:
                    self._current_panel.add_line(f"[#a6e3a1]Result:[/]")
                    for line in content.split("\n")[:30]:
                        self._current_panel.add_line(f"  [#585b70]{line}[/]")
                    if content.count("\n") > 30:
                        self._current_panel.add_line(f"  [#585b70]… truncated[/]")
            else:
                self._current_panel.add_line(f"[#f38ba8]Error: {obs.get('error', '')}[/]")

            self._current_panel.set_complete(ok, summary)
            self._current_panel = None
        self._scroll()

    # ── Diff → 追加到当前面板 ──

    def on_file_diff(self, event: FileDiff) -> None:
        lines = self._make_diff(event.before, event.after)
        scroll = self.query_one("#scroll")
        panels = list(scroll.query(TurnPanel))
        if panels:
            panel = panels[-1]
            panel.add_line(f"[#b4befe]Diff: {event.path}[/]")
            for line in lines[:25]:
                panel.add_line(f"  {line}")
            if len(lines) > 25:
                panel.add_line(f"  [#585b70]… {len(lines) - 25} more[/]")
        self._scroll()

    # ── 最终回答 → 独立展示 ──

    def on_agent_done(self, event: AgentDone) -> None:
        if self._streaming and self._stream_widget:
            self._stream_widget.remove()
            self._streaming = False
            self._stream_widget = None

        result = event.result
        final = result.get("final", "")

        if final:
            self._out("")
            self.query_one("#scroll").mount(
                Static(f"[#cdd6f4]{final}[/]", classes="final-answer", markup=True)
            )
            self._has_final = True

        if result.get("need_user"):
            self._out(f"[#f9e2af]⏸ Waiting for input…[/]")

        self._out("")
        self._stream_buf = ""
        self._current_panel = None
        self._set_input(True)
        self._update_status("ready")
        self._scroll()

    def on_status_change(self, event: StatusChange) -> None:
        self._update_status(event.status, event.detail)

    # ── Actions ──

    def action_clear_screen(self) -> None:
        self.query_one("#scroll", ScrollableContainer).remove_children()

    # ── Helpers ──

    def _out(self, text: str) -> None:
        self.query_one("#scroll").mount(Static(text, markup=True))

    def _scroll(self) -> None:
        self.query_one("#scroll", ScrollableContainer).scroll_end(animate=False)

    def _set_input(self, enabled: bool) -> None:
        inp = self.query_one("#input", Input)
        inp.disabled = not enabled
        if enabled:
            inp.focus()

    def _update_status(self, status: str, detail: str = "") -> None:
        model = self.bridge.model_name
        if status == "ready":
            s = f"[#585b70]{model}[/]"
        elif status == "thinking":
            s = f"[#b4befe]⟳[/] [#585b70]{model} · thinking[/]"
        elif status == "executing":
            tool = f" · {detail}" if detail else ""
            s = f"[#89b4fa]⟳[/] [#585b70]{model}{tool}[/]"
        else:
            s = f"[#585b70]{model} · {status}[/]"
        if self._turn:
            s += f" [#585b70]· turn {self._turn}[/]"
        self.query_one("#status", Static).update(s)

    def _fmt_args(self, args: dict) -> str:
        parts = []
        for k, v in list(args.items())[:2]:
            s = str(v)
            if len(s) > 25:
                s = s[:22] + "…"
            parts.append(f'{k}="{s}"')
        return ", ".join(parts)

    def _fmt_args_full(self, args: dict) -> str:
        import json
        return json.dumps(args, ensure_ascii=False, indent=2)[:300]

    def _obs_summary(self, obs: dict) -> str:
        if "content" in obs:
            c = str(obs["content"])
            n = c.count("\n") + 1
            return f"{n} lines" if n > 3 else c[:40].replace("\n", " ")
        if "stdout" in obs:
            return str(obs["stdout"])[:40].replace("\n", " ")
        if "entries" in obs:
            return f"{len(obs['entries'])} items"
        if "path" in obs:
            return str(obs["path"])
        return "done"

    def _obs_content(self, obs: dict) -> str:
        if "content" in obs:
            return str(obs["content"])
        if "stdout" in obs:
            return str(obs["stdout"])
        return ""

    def _make_diff(self, before: str, after: str) -> list[str]:
        import difflib
        a = before.splitlines(keepends=True)
        b = after.splitlines(keepends=True)
        lines = []
        for line in difflib.unified_diff(a, b, lineterm=""):
            line = line.rstrip()
            if line.startswith("@@"):
                lines.append(f"[#585b70]{line}[/]")
            elif line.startswith("+"):
                lines.append(f"[#a6e3a1]{line}[/]")
            elif line.startswith("-"):
                lines.append(f"[#f38ba8]{line}[/]")
            else:
                lines.append(f"[#585b70]{line}[/]")
        return lines
