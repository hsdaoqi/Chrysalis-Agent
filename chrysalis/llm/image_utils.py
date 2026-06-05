"""图片预处理与屏幕截取。

依赖 mss + Pillow（可选安装：pip install chrysalis[vision]）。
"""

import base64
import ctypes
import ctypes.wintypes as wintypes
import io
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_PIXELS = 1_440_000
JPEG_QUALITY = 75
PW_RENDERFULLCONTENT = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DIB_RGB_COLORS = 0


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


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    exe: str = ""


class WindowCaptureError(RuntimeError):
    pass


def capture_window(
    title: str = "",
    pid: int | None = None,
    exe: str = "",
    max_pixels: int = MAX_PIXELS,
) -> tuple[str, str, WindowInfo]:
    """截取指定 Windows 窗口，返回 (media_type, base64_data, window_info)。"""
    if sys.platform != "win32":
        raise WindowCaptureError("目标窗口截图目前仅支持 Windows")

    window = find_window(title=title, pid=pid, exe=exe)
    if window is None:
        candidates = ", ".join(_format_window_candidate(item) for item in list_windows()[:8])
        hint = f"。可见窗口候选: {candidates}" if candidates else ""
        raise WindowCaptureError(f"未找到匹配窗口{hint}")

    img = _capture_window_image(window.hwnd)
    return (*_encode(img, max_pixels), window)


def find_window(title: str = "", pid: int | None = None, exe: str = "") -> WindowInfo | None:
    """按标题/PID/进程名查找第一个可见窗口。"""
    title_lower = title.strip().lower()
    exe_lower = exe.strip().lower()
    pid_value = int(pid) if pid not in (None, "") else None

    for window in list_windows():
        if title_lower and title_lower not in window.title.lower():
            continue
        if pid_value is not None and window.pid != pid_value:
            continue
        if exe_lower and exe_lower not in window.exe.lower():
            continue
        return window
    return None


def list_windows() -> list[WindowInfo]:
    """列出当前可见、可截图候选窗口。"""
    if sys.platform != "win32":
        return []

    user32 = ctypes.windll.user32
    windows: list[WindowInfo] = []
    exe_cache: dict[int, str] = {}

    enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        title = _get_window_title(hwnd)
        if not title:
            return True
        rect = _get_window_rect(hwnd)
        if rect is None:
            return True
        left, top, right, bottom = rect
        if right - left <= 1 or bottom - top <= 1:
            return True

        pid = _get_window_pid(hwnd)
        if pid not in exe_cache:
            exe_cache[pid] = _get_process_exe(pid)
        windows.append(WindowInfo(hwnd=int(hwnd), title=title, pid=pid, exe=exe_cache[pid]))
        return True

    user32.EnumWindows(enum_proc_type(callback), 0)
    return windows


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


def _capture_window_image(hwnd: int):
    from PIL import Image

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    _configure_capture_api(user32, gdi32)

    rect = _get_window_rect(hwnd)
    if rect is None:
        raise WindowCaptureError("无法读取窗口位置")
    left, top, right, bottom = rect
    width = right - left
    height = bottom - top
    if width <= 1 or height <= 1:
        raise WindowCaptureError("窗口尺寸太小，无法截图")

    hwnd_dc = user32.GetWindowDC(hwnd)
    if not hwnd_dc:
        raise WindowCaptureError("无法获取窗口设备上下文")

    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    old_obj = gdi32.SelectObject(mem_dc, bitmap)
    try:
        rendered = user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
        if not rendered:
            rendered = user32.PrintWindow(hwnd, mem_dc, 0)
        if not rendered:
            raise WindowCaptureError("PrintWindow 截图失败")

        bmi = _bitmap_info(width, height)
        raw = ctypes.create_string_buffer(width * height * 4)
        scan_lines = gdi32.GetDIBits(mem_dc, bitmap, 0, height, raw, ctypes.byref(bmi), DIB_RGB_COLORS)
        if scan_lines != height:
            raise WindowCaptureError("读取窗口位图失败")
        return Image.frombuffer("RGB", (width, height), raw, "raw", "BGRX", 0, 1).copy()
    finally:
        if old_obj:
            gdi32.SelectObject(mem_dc, old_obj)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)


def _get_window_title(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value.strip()


def _get_window_pid(hwnd: int) -> int:
    user32 = ctypes.windll.user32
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def _get_process_exe(pid: int) -> str:
    if not pid:
        return ""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        try:
            size = wintypes.DWORD(260)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return Path(buffer.value).name
        finally:
            kernel32.CloseHandle(handle)

    try:
        import os
        import subprocess

        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            startupinfo=startupinfo,
            timeout=2,
        )
    except Exception:
        return ""
    line = result.stdout.strip().splitlines()
    if not line or "INFO:" in line[0]:
        return ""
    return line[0].split('","', 1)[0].strip('"')


def _configure_capture_api(user32, gdi32) -> None:
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int


def _format_window_candidate(window: WindowInfo) -> str:
    exe = f", exe={window.exe}" if window.exe else ""
    return f"{window.title} (pid={window.pid}{exe})"


def _bitmap_info(width: int, height: int):
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32),
            ("biWidth", ctypes.c_int32),
            ("biHeight", ctypes.c_int32),
            ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16),
            ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32),
            ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32),
            ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [
            ("bmiHeader", BITMAPINFOHEADER),
            ("bmiColors", ctypes.c_uint32 * 3),
        ]

    return BITMAPINFO(
        BITMAPINFOHEADER(
            ctypes.sizeof(BITMAPINFOHEADER),
            width,
            -height,
            1,
            32,
            0,
            width * height * 4,
            0,
            0,
            0,
            0,
        ),
        (ctypes.c_uint32 * 3)(),
    )
