"""图片预处理与屏幕截取。

依赖 mss + Pillow（可选安装：pip install chrysalis[vision]）。
"""

import base64
import io
from pathlib import Path

MAX_PIXELS = 1_440_000
JPEG_QUALITY = 75


def prepare_image_from_path(path: str | Path, max_pixels: int = MAX_PIXELS) -> tuple[str, str]:
    """从文件路径加载图片，返回 (media_type, base64_data)。"""
    from PIL import Image
    img = Image.open(path)
    return _encode(img, max_pixels)


def prepare_image_from_bytes(raw: bytes, max_pixels: int = MAX_PIXELS) -> tuple[str, str]:
    """从原始字节加载图片，返回 (media_type, base64_data)。"""
    from PIL import Image
    img = Image.open(io.BytesIO(raw))
    return _encode(img, max_pixels)


def capture_screen(monitor: int = 0, max_pixels: int = MAX_PIXELS) -> tuple[str, str]:
    """截取屏幕，返回 (media_type, base64_data)。

    monitor: 0=全部屏幕, 1=主屏, 2=副屏...
    """
    import mss
    from PIL import Image

    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[monitor])
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    return _encode(img, max_pixels)


def _encode(img, max_pixels: int) -> tuple[str, str]:
    """统一缩放 + 编码逻辑。"""
    from PIL import Image

    w, h = img.size
    if w * h > max_pixels:
        ratio = (max_pixels / (w * h)) ** 0.5
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return "image/jpeg", base64.b64encode(buf.getvalue()).decode()
