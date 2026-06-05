# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['..\\..\\..\\chrysalis\\electron_runtime.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['configs.config', 'chrysalis.tools.file_tools', 'chrysalis.tools.web_tools', 'chrysalis.tools.code_tools', 'chrysalis.tools.agent_tools', 'chrysalis.tools.vision_tools'],
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
    a.binaries,
    a.datas,
    [],
    name='chrysalis-runtime',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
