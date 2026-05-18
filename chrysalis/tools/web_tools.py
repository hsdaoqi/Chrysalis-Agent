"""网络工具：web_scan, web_execute_js。"""

from pathlib import Path

from chrysalis.browser import BrowserController
from chrysalis.tools.registry import tool
from chrysalis.tools.safety import as_bool

_BROWSER = BrowserController()


@tool("web_scan", "用本机浏览器打开或扫描网页，返回正文和可交互元素摘要", params={
    "url": "要打开的 URL(可选)",
    "tab_id": "切换到指定标签页(可选)",
    "tabs_only": "只返回标签页列表",
    "text_only": "只返回纯文本",
    "wait_ms": "等待页面加载毫秒数(默认1000)",
})
def web_scan(args: dict, workspace: Path | None = None) -> dict:
    return _BROWSER.scan(
        url=args.get("url"),
        tab_id=args.get("tab_id"),
        tabs_only=as_bool(args.get("tabs_only", False)),
        text_only=as_bool(args.get("text_only", False)),
        wait_ms=int(args.get("wait_ms", 1000)),
    )


@tool("web_execute_js", "在当前浏览器标签页执行 JS 代码", params={
    "script": "JS 代码",
    "tab_id": "目标标签页(可选)",
    "timeout_ms": "超时毫秒数(默认10000)",
})
def web_execute_js(args: dict, workspace: Path | None = None) -> dict:
    return _BROWSER.execute_js(
        script=args.get("script", ""),
        tab_id=args.get("tab_id"),
        timeout=int(args.get("timeout_ms", args.get("timeout", 10_000))),
    )
