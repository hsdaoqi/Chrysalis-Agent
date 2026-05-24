# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(r'D:\Project\Chrysalis')

datas = [
    (str(ROOT / 'chrysalis' / 'desktop' / 'qml'), 'qml'),
    (str(ROOT / 'assets' / 'images' / 'chrysalis-icon.ico'), '.'),
]
binaries = []
hiddenimports = ['chrysalis.tools.file_tools', 'chrysalis.tools.web_tools', 'chrysalis.tools.code_tools', 'chrysalis.tools.agent_tools', 'chrysalis.tools.vision_tools', 'configs.config']
tmp_ret = collect_all('PySide6')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['chrysalis\\desktop\\main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Chrysalis',
    icon=str(ROOT / 'assets' / 'images' / 'chrysalis-icon.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Chrysalis',
)
