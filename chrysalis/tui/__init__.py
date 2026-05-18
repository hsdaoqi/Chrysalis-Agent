"""Chrysalis TUI - 类似 Codex CLI 的终端界面。"""


def launch_tui() -> None:
    from chrysalis.tui.app import ChrysalisApp
    app = ChrysalisApp()
    app.run()
