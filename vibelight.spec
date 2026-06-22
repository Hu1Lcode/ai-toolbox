# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec —— 打包成单个 vibelight.exe。

关于 console 设置的说明：
- 桌面悬浮灯/托盘守护进程都不应弹出黑色控制台窗口，所以 console=False（windowed）。
- `vibelight set ...` 由 hook 调用，hook 不读 stdout，windowed 无影响。
- `vibelight status` 人类偶尔要看输出：在 cmd 中直接运行时仍可见，
  只有重定向到管道/文件时 GUI 程序的 stdout 才为空。
  若需稳定捕获 status 输出，请用绿色 Python 直接：python run.py status。

用法：
    pyinstaller vibelight.spec --noconfirm --clean
产物：dist/vibelight.exe（单文件）
"""

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
hiddenimports = collect_submodules("pystray")
# pywin32（桌面悬浮灯用）：collect_submodules 收齐 win32gui/win32con/win32api 等
hiddenimports += collect_submodules("win32com")
# 显式确保桌面灯直接依赖的模块被收入
hiddenimports += ["win32gui", "win32con", "win32api", "pywintypes", "pythoncom"]

a = Analysis(
    ["run.py"],
    pathex=["src"],
    binaries=[],
    datas=[
        ("assets/icons", "assets/icons"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "test", "pydoc_data"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="vibelight",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,   # windowed：托盘无控制台窗口（CLI 子命令输出由 UTF-8 reconfigure 保障）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,       # 图标程序内绘制，无需外部 .ico
)
