"""Chrysalis TUI — Claude Code 风格，带轮次折叠面板。"""

import re

from rich.text import Text
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
    VoiceResult,
    WorkingChange,
    PermissionRequested,
)
from chrysalis.kernel import format_context_usage

SLASH_COMMANDS = [
    ("/help", "显示帮助信息"),
    ("/session", "查看会话列表"),
    ("/session new", "新建会话"),
    ("/session load <n>", "加载第 n 个会话"),
    ("/session delete <n>", "删除第 n 个会话"),
    ("/queue", "查看任务队列"),
    ("/add <task>", "添加任务到队列"),
    ("/cron", "查看 cron 定时任务"),
    ("/cron list", "列出 cron 定时任务"),
    ("/cron create @path", "从 JSON 文件创建 cron 任务"),
    ("/cron tick", "手动执行到期 cron 任务"),
    ("/cron run <id>", "手动执行 cron 任务"),
    ("/cron pause <id>", "暂停 cron 任务"),
    ("/cron resume <id>", "恢复 cron 任务"),
    ("/cron remove <id>", "删除 cron 任务"),
    ("/permissions", "查看权限等级和永久授权"),
    ("/exit", "退出"),
]

KEYBINDINGS_HELP = [
    ("Ctrl+C", "退出"),
    ("Ctrl+L", "清屏"),
    ("Ctrl+G", "跳转到历史问题"),
    ("Ctrl+R", "语音输入（按一次录音，再按停止并转写）"),
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
            detail.update(Text("\n".join(_plain_turn_lines(self._lines)), style="#585b70"))
        else:
            detail.update(Text("(empty)", style="#585b70"))


class TodoPanel(Static):
    DEFAULT_CSS = """
    TodoPanel {
        height: auto;
        max-height: 8;
        background: #050505;
        border-top: solid #2b2b3f;
        padding: 0;
        display: none;
    }
    """

    def __init__(self) -> None:
        super().__init__("")
        self._snapshot: dict = {}

    def set_snapshot(self, snapshot: dict) -> None:
        self._snapshot = snapshot if isinstance(snapshot, dict) else {}
        self._refresh_view()

    def clear(self) -> None:
        self._snapshot = {}
        self.display = False
        self.update("")

    def on_mount(self) -> None:
        self._refresh_view()

    def _refresh_view(self) -> None:
        snapshot = self._snapshot
        todos = snapshot.get("todos") or []
        total = int(snapshot.get("total_count", len(todos)))
        pending = int(snapshot.get("pending_count", len([item for item in todos if item.get("status") != "completed"])))
        goal = str(snapshot.get("goal", "")).strip()
        active_id = str(snapshot.get("active_todo_id", ""))

        if not todos or pending <= 0:
            self.display = False
            self.update("")
            return

        self.display = True
        header = f"[#cdd6f4]TODO[/] [#585b70]{pending}/{total} pending[/]"
        if goal:
            header += f" [#585b70]|[/] [#cdd6f4]{goal}[/]"
        lines = [header]

        pending_items = [item for item in todos if str(item.get("status", "pending")) != "completed"]
        completed_items = [item for item in todos if str(item.get("status", "pending")) == "completed"]
        visible_items, hidden_pending = _compact_todo_items(pending_items)

        for item in visible_items:
            if item is None:
                lines.append(f"[#585b70]... +{hidden_pending} pending[/]")
                continue
            title = str(item.get("title", "")).strip() or "(untitled)"
            note = str(item.get("note", "")).strip()
            item_id = str(item.get("id", ""))
            status = str(item.get("status", "pending"))
            active = item_id == active_id
            prefix = "->" if active else "-"
            suffix = f" [#585b70]{note}[/]" if note else ""
            mark = "[#a6e3a1]x[/]" if status == "completed" else "[#f9e2af]o[/]"
            style = "[#b4befe bold]" if active else "[#cdd6f4]"
            lines.append(f"{mark} {style}{prefix} {title}[/]{suffix}")

        if completed_items:
            for item in completed_items[-2:]:
                title = str(item.get("title", "")).strip() or "(untitled)"
                note = str(item.get("note", "")).strip()
                suffix = f" [#585b70]{note}[/]" if note else ""
                lines.append(f"[#a6e3a1]x[/] [#585b70]- {title}[/]{suffix}")

        self.update(Text.from_markup("\n".join(lines)))


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
        height: auto;
        max-height: 12;
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
    #choice-hint {
        height: auto;
        max-height: 8;
        padding: 0;
        color: #cdd6f4;
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
        Binding("ctrl+c", "interrupt_or_quit", show=False),
        Binding("ctrl+l", "clear_screen", show=False),
        Binding("ctrl+g", "jump_to_message", show=False),
        Binding("ctrl+r", "toggle_recording", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.bridge = AgentBridge(self)
        self.todo_panel = TodoPanel()
        self._streaming = False
        self._stream_widget: Static | None = None
        self._stream_buf = ""
        self._turn = 0
        self._current_panel: TurnPanel | None = None
        self._has_final = False
        self._user_messages: list[tuple[str, Static]] = []
        self._autocomplete_widget: Static | None = None
        self._choice_widget: Static | None = None
        self._pending_choices: list[dict] = []
        self._choice_index = 0
        self._permission_waiting = False
        self._voice_recorder = None

    def compose(self) -> ComposeResult:
        yield ScrollableContainer(id="scroll")
        with Vertical(id="bottom"):
            yield self.todo_panel
            with Vertical(id="input-row"):
                yield Static("[#b4befe]>[/]", id="prompt")
                yield Input(id="input")
            yield Static("", id="status")

    def on_mount(self) -> None:
        self._out("[#b4befe bold]Chrysalis[/] [#585b70]v0.1 · autonomous agent[/]")
        self._out("[#585b70]Type a task, Ctrl+K to interrupt, or press Ctrl+C to exit.[/]")
        self._out("")
        self._update_status("ready")
        self.query_one("#input", Input).focus()

    # ── Input ──

    def on_input_submitted(self, event: Input.Submitted) -> None:
        task = event.value.strip()
        if self._pending_choices and not task:
            task = str(self._pending_choices[self._choice_index].get("label", "")).strip()
        if not task:
            return
        event.input.clear()
        self._dismiss_autocomplete()
        if self._permission_waiting:
            self.bridge.answer_permission(task)
            self._permission_waiting = False
            self._dismiss_choices()
            self._set_input(False)
            self._update_status("thinking")
            return
        self._dismiss_choices()

        if task.lower() in {"/help", "/h", "/?"}:
            self._show_help()
            return

        if task.split()[0].lower() in {"/session", "/sessions", "/s"}:
            self._handle_session_command(task)
            return

        if task.split()[0].lower() == "/cron":
            self._handle_cron_command(task)
            return

        if task.split()[0].lower() in {"/permissions", "/permission", "/perm"}:
            self._handle_permission_command(task)
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
        context_line = format_context_usage(result.get("context"))
        if context_line:
            self._out(f"[#585b70]{context_line}[/]")

        if result.get("need_user"):
            self._out(f"[#f9e2af]⏸ Waiting for input…[/]")
            self._show_choices(result)
        else:
            self.todo_panel.clear()

        self._out("")
        self._stream_buf = ""
        self._current_panel = None
        self._set_input(True)
        self._update_status("ready")
        self._scroll()

    def on_status_change(self, event: StatusChange) -> None:
        self._update_status(event.status, event.detail)

    def on_working_change(self, event: WorkingChange) -> None:
        self.todo_panel.set_snapshot(event.snapshot)

    def on_permission_requested(self, event: PermissionRequested) -> None:
        self._permission_waiting = True
        self._show_permission_request(event.request)
        self._set_input(True)
        self._update_status("approval")
        self._scroll()

    # ── Actions ──

    def action_clear_screen(self) -> None:
        self.query_one("#scroll", ScrollableContainer).remove_children()
        self._user_messages.clear()

    def action_interrupt_or_quit(self) -> None:
        if self._is_busy():
            self.bridge.cancel_task()
            self.todo_panel.clear()
            self._update_status("interrupting")
            return
        self.action_quit()

    def _is_busy(self) -> bool:
        return self._streaming or self._current_panel is not None or self._permission_waiting

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
        if self._pending_choices and event.key in {"up", "down"} and inp.has_focus:
            event.prevent_default()
            event.stop()
            delta = -1 if event.key == "up" else 1
            self._move_choice(delta)
        elif self._pending_choices and event.key in {str(i) for i in range(1, 10)} and inp.has_focus:
            index = int(event.key) - 1
            if index < len(self._pending_choices):
                event.prevent_default()
                event.stop()
                self._choice_index = index
                self._render_choices()
                choice = str(self._pending_choices[self._choice_index].get("label", "")).strip()
                inp.value = choice
                inp.cursor_position = len(choice)
                inp.action_submit()
        elif self._pending_choices and event.key == "enter" and inp.has_focus and not inp.value.strip():
            event.prevent_default()
            event.stop()
            choice = str(self._pending_choices[self._choice_index].get("label", "")).strip()
            inp.value = choice
            inp.cursor_position = len(choice)
            inp.action_submit()
        elif event.key == "tab" and inp.has_focus:
            event.prevent_default()
            event.stop()
            self._handle_tab_complete()
        elif event.key == "escape" and self._autocomplete_widget:
            self._dismiss_autocomplete()
        elif event.key == "escape" and self._permission_waiting:
            event.prevent_default()
            event.stop()
            self.bridge.answer_permission("拒绝")
            self._permission_waiting = False
            self._dismiss_choices()
            self.todo_panel.clear()
            self._set_input(False)
            self._update_status("thinking")

    def on_input_changed(self, event: Input.Changed) -> None:
        text = event.value
        if text.startswith("/") and len(text) >= 2:
            self._show_autocomplete(text)
        else:
            self._dismiss_autocomplete()
        if self._pending_choices and text.strip():
            self._dismiss_choices(keep_pending=True)

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

    def _show_choices(self, result: dict) -> None:
        choices = result.get("options") or []
        if not choices:
            choices = [
                {"label": str(candidate), "description": ""}
                for candidate in result.get("candidates", [])
                if str(candidate).strip()
            ]
        self._pending_choices = [choice for choice in choices if str(choice.get("label", "")).strip()]
        self._choice_index = 0
        if self._pending_choices:
            self._render_choices()

    def _render_choices(self) -> None:
        parts = []
        for index, choice in enumerate(self._pending_choices):
            label = str(choice.get("label", "")).strip()
            number = f"{index + 1}."
            if index == self._choice_index:
                parts.append(f"[#b4befe]> {number} {label}[/]")
            else:
                parts.append(f"[#585b70]  {number} {label}[/]")
        hint = "\n".join(parts)
        if self._choice_widget is None:
            self._choice_widget = Static(hint, markup=True, id="choice-hint")
            bottom = self.query_one("#bottom", Vertical)
            bottom.mount(self._choice_widget, before=0)
        else:
            self._choice_widget.update(hint)
        self._update_status("Enter to select · ↑/↓ navigate · Esc to cancel")

    def _move_choice(self, delta: int) -> None:
        if not self._pending_choices:
            return
        self._choice_index = (self._choice_index + delta) % len(self._pending_choices)
        self._render_choices()

    def _dismiss_choices(self, keep_pending: bool = False) -> None:
        if self._choice_widget:
            self._choice_widget.remove()
            self._choice_widget = None
        if not keep_pending:
            self._pending_choices = []
            self._choice_index = 0

    def _show_permission_request(self, request: dict) -> None:
        question = str(request.get("question", "需要确认权限"))
        tool = str(request.get("tool", "") or "command")
        summary = _permission_summary_from_request(request)
        self._out("")
        self._out(f"[#b4befe]{tool}[/]")
        self._out(f"[#cdd6f4]{summary}[/]")
        self._out("[#585b70]This command requires approval[/]")
        self._out(f"[#cdd6f4]{question}[/]")
        self._out("[#585b70]Do you want to proceed?[/]")
        self._show_choices(request)

    # ── Voice input ──

    def _get_voice_recorder(self):
        if self._voice_recorder is None:
            try:
                from chrysalis.voice import VoiceRecorder
                self._voice_recorder = VoiceRecorder()
            except ImportError:
                return None
        return self._voice_recorder

    def action_toggle_recording(self) -> None:
        recorder = self._get_voice_recorder()
        if recorder is None:
            self._out("[#f38ba8]语音功能未安装，请运行: pip install -e \".[voice]\"[/]")
            return

        if not recorder.is_recording:
            recorder.start_recording()
            self._update_status("recording")
        else:
            self._update_status("transcribing")
            recorder.stop_and_transcribe(on_done=self._on_voice_done)

    def _on_voice_done(self, text: str) -> None:
        self.call_from_thread(self.post_message, VoiceResult(text))

    def on_voice_result(self, event: VoiceResult) -> None:
        self._update_status("ready")
        if event.text:
            inp = self.query_one("#input", Input)
            inp.value = event.text
            inp.cursor_position = len(event.text)
            inp.focus()
        else:
            self._out("[#585b70]未识别到语音内容[/]")

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
            self._replay_history(kernel.llm.history)
            self._out(f"[#a6e3a1]已加载会话：{s['title']} ({s['turns']} turns)[/]")
            self._out("")
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

    def _handle_cron_command(self, raw: str) -> None:
        from chrysalis.kernel import _handle_cron_command

        def emit(text: str) -> None:
            self._out(str(text))

        _handle_cron_command(self.bridge.kernel, raw, emit)

    def _handle_permission_command(self, raw: str) -> None:
        from chrysalis.kernel import _handle_permission_command

        def emit(text: str) -> None:
            self._out(str(text))

        _handle_permission_command(self.bridge.kernel, raw, emit)

    # ── Helpers ──

    def _replay_history(self, history: list[dict]) -> None:
        """将已加载的会话历史以简洁模式渲染到 scroll 区域。"""
        scroll = self.query_one("#scroll")
        # 清空当前显示
        for child in list(scroll.children):
            child.remove()
        self._user_messages = []

        self._out("[#585b70]── 会话历史 ──[/]")
        self._out("")

        for msg in history:
            role = msg.get("role", "")
            blocks = msg.get("blocks", [])

            if role == "user":
                text = self._extract_user_text(blocks)
                if text:
                    widget = Static(f"[bold #cdd6f4]> {text}[/]", markup=True)
                    scroll.mount(widget)
                    self._user_messages.append((text, widget))
                    self._out("")

            elif role == "assistant":
                text = self._extract_assistant_text(blocks)
                tool_names = [
                    b.get("name", "")
                    for b in blocks
                    if b.get("type") == "tool_use" and b.get("name")
                ]
                if tool_names:
                    tools_str = ", ".join(tool_names)
                    self._out(f"  [#585b70]⟳ {tools_str}[/]")
                if text:
                    md_widget = Markdown(text, classes="final-md")
                    scroll.mount(md_widget)
                    self._out("")

        self._out("[#585b70]── 历史结束 ──[/]")
        self._out("")
        self._scroll()

    def _extract_user_text(self, blocks: list[dict]) -> str:
        """从 user message blocks 中提取纯文本（跳过 tool_result）。"""
        parts = []
        for b in blocks:
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
        text = "".join(parts).strip()
        if len(text) > 200:
            text = text[:200] + "..."
        return text

    def _extract_assistant_text(self, blocks: list[dict]) -> str:
        """从 assistant message blocks 中提取最终回答文本。"""
        parts = []
        for b in blocks:
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
        return "".join(parts).strip()

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
            s = f"[#b4befe]?[/] [#585b70]{model} ? thinking[/]"
        elif status == "executing":
            tool = f" ? {detail}" if detail else ""
            s = f"[#89b4fa]?[/] [#585b70]{model}{tool}[/]"
        elif status == "interrupting":
            s = f"[#f38ba8]?[/] [#585b70]{model} ? interrupting[/]"
        elif status == "recording":
            s = f"[#f38ba8]?[/] [#585b70]recording... (Ctrl+R to stop)[/]"
        elif status == "transcribing":
            s = f"[#a6e3a1]?[/] [#585b70]transcribing...[/]"
        else:
            s = f"[#585b70]{model} ? {status}[/]"
        if self._turn:
            s += f" [#585b70]? turn {self._turn}[/]"
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


def _permission_summary_from_request(request: dict) -> str:
    tool = str(request.get("tool", "") or "command")
    details = request.get("details") if isinstance(request.get("details"), dict) else {}
    if tool == "code_run":
        code_type = str(details.get("code_type", "code"))
        preview = str(details.get("preview", "")).strip()
        first_line = preview.splitlines()[0] if preview else ""
        if len(first_line) > 120:
            first_line = first_line[:119] + "…"
        return f"{code_type} command" + (f"  {first_line}" if first_line else "")
    if tool in {"file_write", "file_patch", "file_read"}:
        return str(details.get("path", request.get("question", "")))
    if tool == "web_scan":
        return str(details.get("url", "(current tab)"))
    return str(request.get("question", tool))


def _compact_todo_items(items: list[dict]) -> tuple[list[dict | None], int]:
    if len(items) <= 4:
        return items, 0
    hidden = len(items) - 4
    return [items[0], items[1], None, items[-2], items[-1]], hidden


_RICH_TAG_RE = re.compile(r"\[/?(?:#[0-9a-fA-F]{3,8}|[a-zA-Z][a-zA-Z0-9_ -]*(?: [^\]]*)?)\]")


def _plain_turn_lines(lines: list[str]) -> list[str]:
    """Turn details contain arbitrary model/tool text, so render them as plain text."""

    return [_RICH_TAG_RE.sub("", line) for line in lines]
