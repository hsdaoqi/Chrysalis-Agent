"""
ocr_utils — 本地 OCR 工具，基于 rapidocr-onnxruntime。

用法:
    from ocr_utils import ocr_image, ocr_screen, ocr_window
    results = ocr_screen()  # [{"text": "...", "box": [x1,y1,x2,y2], "score": 0.95}, ...]
"""

from __future__ import annotations

import time
from pathlib import Path

_ocr_engine = None


def _get_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    return _ocr_engine


def ocr_image(img) -> list[dict]:
    """对 PIL Image 或图片路径执行 OCR。

    Returns:
        list[dict]: [{"text": str, "box": [x1,y1,x2,y2], "score": float}, ...]
    """
    from PIL import Image
    import numpy as np

    if isinstance(img, (str, Path)):
        img = Image.open(img)

    if img.mode != "RGB":
        img = img.convert("RGB")

    arr = np.array(img)
    engine = _get_engine()
    result, _ = engine(arr)

    if not result:
        return []

    items = []
    for line in result:
        box_points, text, score = line
        xs = [p[0] for p in box_points]
        ys = [p[1] for p in box_points]
        items.append({
            "text": text,
            "box": [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
            "score": round(float(score), 3),
        })
    return items


def ocr_screen(bbox=None) -> list[dict]:
    """截取屏幕区域并 OCR。

    Args:
        bbox: (left, top, right, bottom) 物理像素坐标，None=全屏

    Returns:
        list[dict]: OCR 结果
    """
    from PIL import ImageGrab

    try:
        import ljqCtrl
        if bbox:
            grab_bbox = tuple(int(v * ljqCtrl.dpi_scale) for v in bbox)
        else:
            grab_bbox = None
    except ImportError:
        grab_bbox = bbox

    img = ImageGrab.grab(grab_bbox)
    return ocr_image(img)


def ocr_window(hwnd) -> list[dict]:
    """截取指定窗口并 OCR。

    Args:
        hwnd: 窗口句柄 (int)

    Returns:
        list[dict]: OCR 结果
    """
    import win32gui
    from PIL import ImageGrab

    try:
        import ljqCtrl
        scale = ljqCtrl.dpi_scale
    except ImportError:
        scale = 1.0

    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    rect = win32gui.GetWindowRect(hwnd)
    bbox = tuple(int(v / scale) for v in rect)
    img = ImageGrab.grab(bbox)
    return ocr_image(img)


def ocr_text(img) -> str:
    """简化接口：返回所有识别文本拼接为字符串。"""
    results = ocr_image(img)
    return "\n".join(r["text"] for r in results)
