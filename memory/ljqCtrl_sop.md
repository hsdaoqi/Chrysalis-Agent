# ljqCtrl 使用与坐标转换 SOP

> **must update working memory**：`ljqCtrl一律使用物理坐标｜禁pyautogui｜操作前先gw激活窗口`

## 0. API 快速参考

- `ljqCtrl.dpi_scale`: float (缩放系数 = 逻辑宽度 / 物理宽度)
- `ljqCtrl.Click(x, y)`: 左键单击，物理坐标
- `ljqCtrl.RightClick(x, y)`: 右键单击
- `ljqCtrl.DClick(x, y)`: 双击
- `ljqCtrl.Press(cmd, staytime=0)`: 键盘快捷键，如 `Press('ctrl+c')`
- `ljqCtrl.MoveTo(x, y)`: 仅移动光标
- `ljqCtrl.Scroll(x, y, clicks)`: 滚轮，clicks>0 向上
- `ljqCtrl.GrabWindow(hwnd)` -> PIL Image: 窗口截图
- `ljqCtrl.GrabScreen(bbox)` -> PIL Image: 屏幕区域截图
- `ljqCtrl.FindBlock(template, wrect, threshold)` -> ((cx, cy), found): 模板匹配

## 1. 环境载入

memory/ 已在 code_run 的 sys.path 中，直接 import：
```python
import pygetwindow as gw
import ljqCtrl
```

## 2. 核心：High-DPI 物理坐标换算

`ljqCtrl` 的 Click/MoveTo 接收**物理像素坐标**（即截图中的像素坐标）。
`pygetwindow` 返回的是**逻辑坐标**，需要转换。

- **换算公式**：`物理坐标 = 逻辑坐标 / ljqCtrl.dpi_scale`

## 3. 标准操作流程

1. **激活窗口**：
```python
win = gw.getWindowsWithTitle('窗口标题')[0]
win.restore()
win.activate()
import time; time.sleep(0.3)
```

2. **坐标计算与点击**：
```python
# 从 pygetwindow 获取逻辑坐标
lx, ly = win.left + 100, win.top + 50
# 转换为物理坐标
px, py = lx / ljqCtrl.dpi_scale, ly / ljqCtrl.dpi_scale
ljqCtrl.Click(px, py)
```

3. **从截图坐标点击**（截图坐标 = 物理坐标，直接用）：
```python
# 截图中看到按钮在 (350, 200)
ljqCtrl.Click(350, 200)
```

## 4. 文本输入

ljqCtrl 没有 TypeText。向输入框键入文本：
```python
import pyperclip
ljqCtrl.Click(x, y)  # 点击输入框
ljqCtrl.Press('ctrl+a')  # 全选已有内容
pyperclip.copy('要输入的文本')
ljqCtrl.Press('ctrl+v')  # 粘贴
```

## 5. 避坑指南

- **一律使用物理坐标**：传给 ljqCtrl 的坐标必须是物理坐标。从 pygetwindow 获取的逻辑坐标需先 `/ dpi_scale`。
- **操作前必须激活窗口**：用 `gw.getWindowsWithTitle()` + `activate()`。
- **ClientToScreen 陷阱**：`win32gui.GetWindowRect` 包含标题栏和边框。点击截图内元素时，用 `win32gui.ClientToScreen(hwnd, (0, 0))` 获取客户区原点。
- **DPI 一致性**：未调用 `SetProcessDPIAware()` 时，win32gui 返回逻辑坐标。要么全程用 `/ dpi_scale` 转换，要么先 `ctypes.windll.user32.SetProcessDPIAware()` 后直接用物理坐标。
- **禁止 pyautogui**：会与 win32api 冲突。
