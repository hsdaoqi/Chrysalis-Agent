"""Chrysalis TUI — Claude Code 风格，带轮次折叠面板。"""

from textual import work, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.widgets import Static, Input, Collapsible, Markdown, OptionList
from textual.widgets.option_list import Option
from textual.screen import ModalScreen

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

SLASH_COMMANDS = [
    ("/help", "显示帮助信息"),
    ("/session", "查看会话列表"),
    ("/session new", "新建会话"),
    ("/session load <n>", "加载第 n 个会话"),
    ("/session delete <n>", "删除第 n 个会话"),
    ("/queue", "查看任务队列"),
    ("/add <task>", "添加任务到队列"),
    ("/exit", "退出"),
]

KEYBINDINGS_HELP = [
    ("Ctrl+C", "退出"),
    ("Ctrl+L", "清屏"),
    ("Ctrl+G", "跳转到历史问题"),
    ("Tab", "补全 / 命令"),
    ("Esc", "关闭弹窗"),
]


class JumpScreen(ModalScreen):
    """Ctrl+G 跳转列表：显示所有用户问题，选择后滚动到对应位置。"""

    CSS = """
    JumpScreen {
        align: center middle;
    }
    #jump-panel {
        width: 70%;
        max-height: 60%;
        background: #1e1e2e;
        border: solid #585b70;
        padding: 1 2;
    }
    #jump-title {
        color: #b4befe;
        text-style: bold;
        margin-bottom: 1;
    }
    #jump-list {
        height: 1fr;
        background: #1e1e2e;
        scrollbar-size: 1 1;
    }
    #jump-list > .option-list--option-highlighted {
        background: #313244;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_jump", show=False),
    ]

    def __init__(self, items: list[tuple[str, "Static"]]) -> None:
        super().__init__()
        self._items = items

    def compose(self) -> ComposeResult:
        with Vertical(id="jump-panel"):
            yield Static("[#b4befe]Jump to message[/] [#585b70](Enter to select, Esc to cancel)[/]", id="jump-title")
            options = []
            for i, (text, _widget) in enumerate(self._items, 1):
                display = text[:80] + "…" if len(text) > 80 else text
                options.append(Option(f"[#b4befe]{i}.[/] {display}", id=str(i - 1)))
            yield OptionList(*options, id="jump-list")

    def on_mount(self) -> None:
        self.query_one("#jump-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        idx = int(event.option.id)
        _, widget = self._items[idx]
        self.dismiss(widget)

    def action_dismiss_jump(self) -> None:
        self.dismiss(None)


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
        from rich.markup import escape
        tool = escape(self.tool)
        args = escape(self.args_brief)
        summary = escape(self._summary)
        if self._status == "running":
            return f"[#b4befe]Turn {self.turn_num}[/] [#585b70]│[/] [#89b4fa]⏵[/] [#cdd6f4]{tool}[/][#585b70]({args})[/] [#585b70]…[/]"
        elif self._status == "ok":
            return f"[#b4befe]Turn {self.turn_num}[/] [#585b70]│[/] [#a6e3a1]✓[/] [#cdd6f4]{tool}[/] [#585b70]— {summary}[/]"
        else:
            return f"[#b4befe]Turn {self.turn_num}[/] [#585b70]│[/] [#f38ba8]✗[/] [#cdd6f4]{tool}[/] [#585b70]— {summary}[/]"

    def _refresh_detail(self) -> None:
        detail = self.query_one(".turn-detail", Static)
        if self._lines:
            from rich.markup import escape
            detail.update(escape("\n".join(self._lines)))
        else:
            detail.update("[#585b70](empty)[/]")


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
    #autocomplete-hint {
        height: 1;
        padding: 0;
        color: #585b70;
    }
    .final-answer {
        margin: 1 0 0 0;
        padding: 0 0;
        color: #cdd6f4;
    }
    .final-md {
        margin: 1 0 0 0;
        padding: 0;
        background: transparent;
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
        Binding("ctrl+g", "jump_to_message", show=False),
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
        self._user_messages: list[tuple[str, Static]] = []
        self._autocomplete_widget: Static | None = None

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
        self._dismiss_autocomplete()

        if task.lower() in {"/help", "/h", "/?"}:
            self._show_help()
            return

        if task.split()[0].lower() in {"/session", "/sessions", "/s"}:
            self._handle_session_command(task)
            return

        msg_widget = Static(f"[bold #cdd6f4]> {task}[/]", markup=True)
        self.query_one("#scroll").mount(msg_widget)
        self._user_messages.append((task, msg_widget))
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
            md_widget = Markdown(final, classes="final-md")
            self.query_one("#scroll").mount(md_widget)
            self._has_final = True

        usage_line = self._format_usage(result)
        if usage_line:
            self._out(f"[#585b70]{usage_line}[/]")

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
        self._user_messages.clear()

    def action_jump_to_message(self) -> None:
        if not self._user_messages:
            return
        self.push_screen(JumpScreen(self._user_messages), self._on_jump_selected)

    def _on_jump_selected(self, widget: Static | None) -> None:
        if widget is None:
            return
        widget.scroll_visible(animate=True)

    def _show_help(self) -> None:
        self._out("[#b4befe bold]Keybindings[/]")
        for key, desc in KEYBINDINGS_HELP:
            self._out(f"  [#89b4fa]{key:<10}[/] [#cdd6f4]{desc}[/]")
        self._out("")
        self._out("[#b4befe bold]Commands[/]")
        for cmd, desc in SLASH_COMMANDS:
            self._out(f"  [#89b4fa]{cmd:<22}[/] [#cdd6f4]{desc}[/]")
        self._out("")

    # ── Tab autocomplete ──

    def on_key(self, event) -> None:
        inp = self.query_one("#input", Input)
        if event.key == "tab" and inp.has_focus:
            event.prevent_default()
            event.stop()
            self._handle_tab_complete()
        elif event.key == "escape" and self._autocomplete_widget:
            self._dismiss_autocomplete()

    def on_input_changed(self, event: Input.Changed) -> None:
        text = event.value
        if text.startswith("/") and len(text) >= 2:
            self._show_autocomplete(text)
        else:
            self._dismiss_autocomplete()

    def _handle_tab_complete(self) -> None:
        inp = self.query_one("#input", Input)
        text = inp.value
        if not text.startswith("/"):
            return
        matches = [cmd for cmd, _ in SLASH_COMMANDS if cmd.startswith(text)]
        if len(matches) == 1:
            completed = matches[0]
            if "<" not in completed:
                completed += " "
            inp.value = completed
            inp.cursor_position = len(inp.value)
            self._dismiss_autocomplete()
        elif matches:
            prefix = _common_prefix(matches)
            if len(prefix) > len(text):
                inp.value = prefix
                inp.cursor_position = len(inp.value)

    def _show_autocomplete(self, text: str) -> None:
        matches = [(cmd, desc) for cmd, desc in SLASH_COMMANDS if cmd.startswith(text)]
        if not matches:
            self._dismiss_autocomplete()
            return
        lines = "  ".join(f"[#89b4fa]{cmd}[/]" for cmd, _ in matches)
        hint = f"[#585b70]{lines}[/]"
        if self._autocomplete_widget is None:
            self._autocomplete_widget = Static(hint, markup=True, id="autocomplete-hint")
            bottom = self.query_one("#bottom", Vertical)
            bottom.mount(self._autocomplete_widget, before=0)
        else:
            self._autocomplete_widget.update(hint)

    def _dismiss_autocomplete(self) -> None:
        if self._autocomplete_widget:
            self._autocomplete_widget.remove()
            self._autocomplete_widget = None

    # ── Session command ──

    def _handle_session_command(self, raw: str) -> None:
        parts = raw.strip().split(maxsplit=2)
        sub = parts[1].lower() if len(parts) > 1 else ""
        arg = parts[2] if len(parts) > 2 else ""
        kernel = self.bridge.kernel

        if sub == "new":
            sid = kernel.new_session()
            self._out(f"[#a6e3a1]已创建新会话：{sid}[/]")
            return

        if sub == "load":
            sessions = kernel.list_sessions()
            if not sessions:
                self._out("[#f9e2af]没有可加载的会话。[/]")
                return
            try:
                idx = int(arg) - 1
            except (ValueError, TypeError):
                self._out("[#f38ba8]用法：/session load <编号>[/]")
                return
            if idx < 0 or idx >= len(sessions):
                self._out(f"[#f38ba8]编号无效，范围 1-{len(sessions)}[/]")
                return
            s = sessions[idx]
            kernel.load_session(s["id"])
            self._out(f"[#a6e3a1]已加载会话：{s['title']} ({s['turns']} turns)[/]")
            return

        if sub == "delete":
            sessions = kernel.list_sessions()
            if not sessions:
                self._out("[#f9e2af]没有可删除的会话。[/]")
                return
            try:
                idx = int(arg) - 1
            except (ValueError, TypeError):
                self._out("[#f38ba8]用法：/session delete <编号>[/]")
                return
            if idx < 0 or idx >= len(sessions):
                self._out(f"[#f38ba8]编号无效，范围 1-{len(sessions)}[/]")
                return
            s = sessions[idx]
            kernel.delete_session(s["id"])
            self._out(f"[#a6e3a1]已删除会话：{s['title']}[/]")
            return

        sessions = kernel.list_sessions()
        if not sessions:
            self._out("[#585b70]暂无会话记录。/session new 新建会话[/]")
            return
        current = kernel.session_store.current_id
        self._out("[#b4befe]会话列表：[/]")
        for i, s in enumerate(sessions, 1):
            marker = " [#a6e3a1]*[/]" if s["id"] == current else ""
            self._out(f"  [#cdd6f4]{i}.[/] {s['title']}  [#585b70][{s['model']}] {s['turns']}t  {s['updated_at']}[/]{marker}")
        self._out("[#585b70]  /session load <n> 加载 | /session new 新建 | /session delete <n> 删除[/]")

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

    def _format_usage(self, result: dict) -> str:
        usage = result.get("usage")
        if not usage or not usage.get("total_tokens"):
            return ""
        from chrysalis.llm.types import Usage, _fmt_num
        from chrysalis.llm.usage import _fmt_elapsed
        u = Usage.from_dict(usage)
        elapsed = result.get("elapsed_ms", 0)
        cost = usage.get("cost", 0)
        turns = usage.get("turns", 0)
        parts = [u.format()]
        if cost > 0:
            parts.append(f"~${cost:.4f}")
        if turns:
            parts.append(f"{turns} turns")
        if elapsed:
            parts.append(_fmt_elapsed(elapsed))
        return f"[{' | '.join(parts)}]"


def _common_prefix(strings: list[str]) -> str:
    if not strings:
        return ""
    prefix = strings[0]
    for s in strings[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix
