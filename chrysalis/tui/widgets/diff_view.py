"""文件 diff 渲染 widget。"""

import difflib

from textual.widgets import Static

from chrysalis.tui.theme import ERROR_COLOR, SUCCESS_COLOR, DIM_COLOR


class DiffView(Static):
    """显示 unified diff，带颜色高亮。"""

    DEFAULT_CSS = """
    DiffView {
        padding: 0 2;
        margin: 0 0 1 0;
    }
    """

    def __init__(self, path: str, before: str, after: str) -> None:
        super().__init__()
        self.path = path
        self.diff_text = self._generate_diff(path, before, after)

    def on_mount(self) -> None:
        self.update(self.diff_text)

    def _generate_diff(self, path: str, before: str, after: str) -> str:
        before_lines = before.splitlines(keepends=True)
        after_lines = after.splitlines(keepends=True)
        diff = difflib.unified_diff(
            before_lines, after_lines,
            fromfile=f"{path} (before)",
            tofile=f"{path} (after)",
            lineterm="",
        )
        colored_lines = []
        count = 0
        for line in diff:
            if count >= 50:
                colored_lines.append(f"[{DIM_COLOR}]... (diff truncated)[/]")
                break
            if line.startswith("+++") or line.startswith("---"):
                colored_lines.append(f"[bold]{line.rstrip()}[/]")
            elif line.startswith("@@"):
                colored_lines.append(f"[{DIM_COLOR}]{line.rstrip()}[/]")
            elif line.startswith("+"):
                colored_lines.append(f"[{SUCCESS_COLOR}]{line.rstrip()}[/]")
            elif line.startswith("-"):
                colored_lines.append(f"[{ERROR_COLOR}]{line.rstrip()}[/]")
            else:
                colored_lines.append(line.rstrip())
            count += 1
        return "\n".join(colored_lines) if colored_lines else f"[{DIM_COLOR}](no changes)[/]"
