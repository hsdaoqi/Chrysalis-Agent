"""
ljqCtrl — Win32 API 键鼠控制模块。
严禁 import pyautogui（会污染 win32api 导致逻辑冲突）。

Quick Reference:
- dpi_scale: float (逻辑坐标 = 物理坐标 * dpi_scale)
- Click(x, y): 物理坐标点击
- RightClick(x, y): 右键点击
- DClick(x, y): 双击
- Press(cmd, staytime=0): 键盘快捷键 (e.g. 'ctrl+v')
- MoveTo(x, y): 仅移动光标
- Scroll(x, y, clicks): 滚轮
- GrabWindow(hwnd) -> PIL Image: DPI 安全的窗口截图
"""

import time
import ctypes
import win32api
import win32con

dpi_scale = 1.0
swidth = 1920
sheight = 1080

try:
    _hdc = ctypes.windll.user32.GetDC(0)
    swidth = ctypes.windll.gdi32.GetDeviceCaps(_hdc, 118)  # DESKTOPHORZRES (物理)
    sheight = ctypes.windll.gdi32.GetDeviceCaps(_hdc, 117)  # DESKTOPVERTRES
    ctypes.windll.user32.ReleaseDC(0, _hdc)
    cwidth = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)  # 逻辑
    dpi_scale = cwidth / swidth
except Exception:
    pass


# ── 底层鼠标操作 ──

def MouseDown():
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)


def MouseUp():
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)


def MouseClick(staytime=0.05):
    MouseDown()
    time.sleep(staytime)
    MouseUp()
    time.sleep(0.05)


def MouseDClick(staytime=0.05):
    MouseDown()
    MouseUp()
    time.sleep(0.02)
    MouseDown()
    MouseUp()
    time.sleep(0.05)


def MouseRightClick(staytime=0.05):
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0)
    time.sleep(staytime)
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0)
    time.sleep(0.05)


# ── 光标移动 ──

def SetCursorPos(pos):
    """移动光标到物理坐标 pos=(x, y)，内部自动转换为逻辑坐标。"""
    x, y = int(pos[0] * dpi_scale), int(pos[1] * dpi_scale)
    win32api.SetCursorPos((x, y))
    time.sleep(0.05)


def MoveTo(x, y=None):
    """移动光标到物理坐标，不点击。"""
    if isinstance(x, (tuple, list)):
        x, y = x[0], x[1]
    SetCursorPos((x, y))


# ── 高级点击 ──

def Click(x, y=None):
    """移动到物理坐标 (x, y) 并左键单击。支持 Click((x,y)) 或 Click(x, y)。"""
    if isinstance(x, (tuple, list)):
        x, y = x[0], x[1]
    SetCursorPos((int(x), int(y)))
    MouseClick()


def RightClick(x, y=None):
    """移动到物理坐标 (x, y) 并右键单击。"""
    if isinstance(x, (tuple, list)):
        x, y = x[0], x[1]
    SetCursorPos((int(x), int(y)))
    MouseRightClick()


def DClick(x, y=None):
    """移动到物理坐标 (x, y) 并双击。"""
    if isinstance(x, (tuple, list)):
        x, y = x[0], x[1]
    SetCursorPos((int(x), int(y)))
    MouseDClick()


def Scroll(x, y, clicks=3):
    """在物理坐标 (x, y) 处滚动鼠标滚轮。clicks>0 向上，<0 向下。"""
    SetCursorPos((int(x), int(y)))
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, clicks * 120)
    time.sleep(0.1)


# ── 键盘 ──

VK_CODE = {
    'backspace': 0x08, 'tab': 0x09, 'enter': 0x0D, 'shift': 0x10,
    'ctrl': 0x11, 'alt': 0x12, 'pause': 0x13, 'caps_lock': 0x14,
    'esc': 0x1B, 'escape': 0x1B, 'space': 0x20,
    'page_up': 0x21, 'page_down': 0x22, 'end': 0x23, 'home': 0x24,
    'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28,
    'print_screen': 0x2C, 'ins': 0x2D, 'insert': 0x2D,
    'del': 0x2E, 'delete': 0x2E,
    '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34,
    '5': 0x35, '6': 0x36, '7': 0x37, '8': 0x38, '9': 0x39,
    'a': 0x41, 'b': 0x42, 'c': 0x43, 'd': 0x44, 'e': 0x45,
    'f': 0x46, 'g': 0x47, 'h': 0x48, 'i': 0x49, 'j': 0x4A,
    'k': 0x4B, 'l': 0x4C, 'm': 0x4D, 'n': 0x4E, 'o': 0x4F,
    'p': 0x50, 'q': 0x51, 'r': 0x52, 's': 0x53, 't': 0x54,
    'u': 0x55, 'v': 0x56, 'w': 0x57, 'x': 0x58, 'y': 0x59, 'z': 0x5A,
    'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73,
    'f5': 0x74, 'f6': 0x75, 'f7': 0x76, 'f8': 0x77,
    'f9': 0x78, 'f10': 0x79, 'f11': 0x7A, 'f12': 0x7B,
    'num_lock': 0x90, 'scroll_lock': 0x91,
    '+': 0xBB, ',': 0xBC, '-': 0xBD, '.': 0xBE,
    '/': 0xBF, '`': 0xC0, ';': 0xBA, '[': 0xDB,
    '\\': 0xDC, ']': 0xDD, "'": 0xDE,
    'win': 0x5B, 'apps': 0x5D,
}


def Press(cmd, staytime=0):
    """模拟键盘快捷键。如 Press('ctrl+c'), Press('alt+tab')。"""
    if isinstance(cmd, list):
        keys = [k.lower().strip() for k in cmd]
    else:
        keys = [k.lower().strip() for k in cmd.split('+')]
    for k in keys:
        if k not in VK_CODE:
            raise ValueError(f"未知按键: {k!r}，可用: {', '.join(sorted(VK_CODE.keys())[:20])}...")
        win32api.keybd_event(VK_CODE[k], 0, 0, 0)
        if staytime:
            time.sleep(staytime)
    for k in reversed(keys):
        win32api.keybd_event(VK_CODE[k], 0, win32con.KEYEVENTF_KEYUP, 0)
        if staytime:
            time.sleep(staytime)


# ── 窗口截图 ──

def GrabWindow(hwnd):
    """截取指定窗口的图像（DPI 安全）。返回 PIL Image。"""
    import win32gui
    from PIL import ImageGrab
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    rect = win32gui.GetWindowRect(hwnd)
    bbox = tuple(int(v / dpi_scale) for v in rect)
    return ImageGrab.grab(bbox)


def GrabScreen(bbox=None):
    """截取屏幕区域。bbox=(left, top, right, bottom) 为物理坐标，None 为全屏。"""
    from PIL import ImageGrab
    if bbox:
        bbox = tuple(int(v * dpi_scale) for v in bbox)
    return ImageGrab.grab(bbox)


# ── 模板匹配 ──

def FindBlock(template, wrect=None, threshold=0.8):
    """在屏幕/窗口中查找模板图片。

    Args:
        template: 模板图片路径(str)或 PIL Image
        wrect: 搜索区域 PIL Image 或 (left, top, right, bottom) 物理坐标，None=全屏
        threshold: 匹配阈值

    Returns:
        ((center_x, center_y), is_found): 物理坐标和是否找到
    """
    import numpy as np
    import cv2
    from PIL import ImageGrab, Image

    if isinstance(wrect, Image.Image):
        scr = wrect
        offset = (0, 0)
    else:
        if wrect:
            grab_bbox = tuple(int(v * dpi_scale) for v in wrect)
            offset = (wrect[0], wrect[1])
        else:
            grab_bbox = None
            offset = (0, 0)
        scr = ImageGrab.grab(grab_bbox)

    if isinstance(template, str):
        tpl = Image.open(template)
    else:
        tpl = template

    T = cv2.cvtColor(np.array(tpl), cv2.COLOR_RGB2BGR)
    B = cv2.cvtColor(np.array(scr), cv2.COLOR_RGB2BGR)
    tsh, tsw = T.shape[:2]

    res = cv2.matchTemplate(B, T, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)

    if max_val >= threshold:
        cx = max_loc[0] + tsw // 2 + offset[0]
        cy = max_loc[1] + tsh // 2 + offset[1]
        return (cx, cy), True
    return (0, 0), False
