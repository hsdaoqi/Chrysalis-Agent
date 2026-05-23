import sys, os, json, re, time
from pathlib import Path

# Windows: 隐藏子进程窗口
import subprocess as _sp
_OrigPopen = _sp.Popen.__init__
def _patched_popen_init(self, *a, **k):
    if os.name == 'nt':
        k['creationflags'] = (k.get('creationflags') or 0) | 0x08000000
    _OrigPopen(self, *a, **k)
_sp.Popen.__init__ = _patched_popen_init

# 自定义 excepthook: ImportError 时提示 agent
def _agent_excepthook(t, v, tb):
    sys.__excepthook__(t, v, tb)
    if issubclass(t, (ImportError, ModuleNotFoundError)):
        print(f"\n[Hint] 缺少模块 {v.name if hasattr(v,'name') else v}，请先 pip install。")
sys.excepthook = _agent_excepthook
