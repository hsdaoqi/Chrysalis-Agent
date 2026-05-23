# Vision / 截图 SOP

## 前置规则（必须遵守）

1. **先枚举窗口**：截图前必须用 `pygetwindow` 确认目标窗口存在且已激活。
2. **禁止全屏截图**：能截局部就不截整窗口，能截窗口就不全屏。全屏截图浪费 token 且信息密度低。
3. **能不用 vision API 就不用**：窗口标题 / 本地 OCR 能获取信息时，不调 vision API。

## 决策树

```
需要了解屏幕内容？
├── 只需要文字 → ocr_utils.ocr_window(hwnd) 或 ocr_screen(bbox)
├── 需要理解布局/图像 → 截图 + vision_api.ask_vision()
└── 只需要确认窗口状态 → pygetwindow 枚举标题即可
```

## 快速用法

### 本地 OCR（优先）
```python
import pygetwindow as gw
import ljqCtrl, ocr_utils

win = gw.getWindowsWithTitle('目标窗口')[0]
win.activate()
import time; time.sleep(0.3)

# 方式1: 窗口 OCR
import win32gui
hwnd = win32gui.FindWindow(None, '目标窗口')
results = ocr_utils.ocr_window(hwnd)
for r in results:
    print(f"{r['text']}  @ {r['box']}")

# 方式2: 区域 OCR（物理坐标）
results = ocr_utils.ocr_screen(bbox=(100, 200, 800, 600))
```

### Vision API（需要理解图像内容时）
```python
import ljqCtrl
from vision_api import ask_vision

# 截取窗口
import win32gui
hwnd = win32gui.FindWindow(None, '目标窗口')
img = ljqCtrl.GrabWindow(hwnd)

# 调用多模态 LLM
result = ask_vision(img, prompt="这个界面上有哪些按钮？它们的位置大概在哪里？")
print(result)
```

### screenshot 工具（最简方式）

如果只需要快速看一眼屏幕，直接用 screenshot 工具（无需写代码）。
screenshot 工具会自动截屏并将图片发送给当前对话的 LLM。

## 坐标定位流程

当需要点击截图中的某个元素时：
1. 截取窗口/区域图片
2. 用 OCR 或 vision API 确定元素位置
3. 截图坐标 = 物理坐标，直接传给 `ljqCtrl.Click()`

```python
# 完整示例：找到并点击某个按钮
import win32gui
import ljqCtrl, ocr_utils

hwnd = win32gui.FindWindow(None, '目标窗口')
results = ocr_utils.ocr_window(hwnd)

# 找到"确定"按钮的位置
for r in results:
    if '确定' in r['text']:
        x1, y1, x2, y2 = r['box']
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        # box 坐标是相对于截图的，需要加上窗口偏移
        rect = win32gui.GetWindowRect(hwnd)
        abs_x = rect[0] / ljqCtrl.dpi_scale + cx
        abs_y = rect[1] / ljqCtrl.dpi_scale + cy
        ljqCtrl.Click(abs_x, abs_y)
        break
```
