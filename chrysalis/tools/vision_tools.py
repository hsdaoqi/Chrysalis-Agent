"""视觉工具：截屏、图片识别。"""

from pathlib import Path

from chrysalis.tools.registry import tool


@tool(
    name="screenshot",
    description="截取当前屏幕内容用于视觉分析。返回的图片会自动发送给模型识别。",
    params={"monitor": "显示器编号：0=全部屏幕, 1=主屏, 2=副屏（默认1）"},
)
def screenshot_tool(args: dict, workspace: Path | None = None) -> dict:
    try:
        from chrysalis.llm.image_utils import capture_screen
    except ImportError:
        return {"ok": False, "error": "缺少 vision 依赖，请安装：pip install chrysalis[vision]"}

    monitor = int(args.get("monitor", 1))
    try:
        media_type, data = capture_screen(monitor=monitor)
    except Exception as exc:
        return {"ok": False, "error": f"截屏失败: {exc}"}

    return {
        "ok": True,
        "content": "已截取屏幕截图，图片已附加供视觉分析。",
        "_image": {"media_type": media_type, "data": data},
    }
