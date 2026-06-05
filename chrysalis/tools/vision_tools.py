"""视觉工具: 截图和 OCR。"""

from pathlib import Path

from chrysalis.tools.registry import tool
from chrysalis.tools.safety import safe_path

_OCR_ENGINE = None


@tool(
    name="screenshot",
    description="截取屏幕或指定目标窗口内容用于视觉分析。返回的图片会自动发送给模型识别。",
    params={
        "monitor": "显示器编号: 0=全部屏幕, 1=主屏, 2=副屏(默认1)",
        "window_title": "可选，按窗口标题模糊匹配目标窗口",
        "window_pid": "可选，按进程 PID 精确匹配目标窗口",
        "window_exe": "可选，按进程可执行文件名模糊匹配目标窗口，如 chrome.exe",
    },
)
def screenshot_tool(args: dict, workspace: Path | None = None) -> dict:
    try:
        from chrysalis.llm.image_utils import capture_screen, capture_window
    except ImportError:
        return {"ok": False, "error": "缺少 vision 依赖，请安装: pip install chrysalis[vision]"}

    monitor = int(args.get("monitor", 1))
    window_title = str(args.get("window_title") or "").strip()
    raw_window_pid = args.get("window_pid")
    window_pid = _coerce_int(raw_window_pid)
    window_exe = str(args.get("window_exe") or "").strip()
    if raw_window_pid not in (None, "") and window_pid is None:
        return {"ok": False, "error": "window_pid 必须是数字"}
    try:
        if window_title or window_pid is not None or window_exe:
            media_type, data, window = capture_window(
                title=window_title,
                pid=window_pid,
                exe=window_exe,
            )
            return {
                "ok": True,
                "content": f"已截取目标窗口截图：{window.title}",
                "window": {
                    "title": window.title,
                    "pid": window.pid,
                    "exe": window.exe,
                    "hwnd": window.hwnd,
                },
                "_image": {"media_type": media_type, "data": data},
            }

        media_type, data = capture_screen(monitor=monitor)
    except Exception as exc:
        return {"ok": False, "error": f"截图失败: {exc}"}

    return {
        "ok": True,
        "content": "已截取屏幕截图，图片已附加供视觉分析。",
        "_image": {"media_type": media_type, "data": data},
    }


def _coerce_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@tool(
    name="ocr",
    description="识别图片中的文字，返回纯文本和位置信息。",
    params={"path": "图片文件路径"},
)
def ocr_tool(args: dict, workspace: Path | None = None) -> dict:
    path = args.get("path", "")
    if not path:
        return {"ok": False, "error": "path 不能为空"}

    try:
        target = safe_path(path, workspace)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if not target.is_file():
        return {"ok": False, "path": str(target), "error": "图片文件不存在"}

    try:
        engine = _get_ocr_engine()
    except ImportError:
        return {"ok": False, "error": "缺少 OCR 依赖，请安装: pip install chrysalis[ocr]"}
    except Exception as exc:
        return {"ok": False, "error": f"OCR 引擎初始化失败: {exc}"}

    try:
        raw_result = engine(str(target))
    except Exception as exc:
        return {"ok": False, "path": str(target), "error": f"OCR 识别失败: {exc}"}

    lines = _parse_ocr_result(raw_result)
    text = "\n".join(item["text"] for item in lines if item["text"]).strip()
    return {
        "ok": True,
        "path": str(target),
        "text": text,
        "lines": lines,
        "count": len(lines),
    }


def _get_ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE

    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise ImportError("missing rapidocr-onnxruntime") from exc

    _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _parse_ocr_result(raw_result) -> list[dict]:
    if not raw_result:
        return []

    items = raw_result[0] if isinstance(raw_result, tuple) else raw_result
    parsed: list[dict] = []
    for index, item in enumerate(items or []):
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        parsed.append({
            "index": index,
            "box": item[0],
            "text": str(item[1]).strip(),
            "score": _coerce_score(item[2] if len(item) > 2 else None),
        })

    parsed.sort(key=_ocr_sort_key)
    return parsed


def _ocr_sort_key(item: dict) -> tuple[float, float, int]:
    box = item.get("box") or []
    points: list[tuple[float, float]] = []
    for point in box:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                points.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError):
                continue
    if not points:
        return (0.0, 0.0, int(item.get("index", 0)))
    top = min(y for _, y in points)
    left = min(x for x, _ in points)
    return (top, left, int(item.get("index", 0)))


def _coerce_score(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
