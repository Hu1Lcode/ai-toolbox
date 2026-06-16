"""开发期启动器：把 src 加入 sys.path 后运行 vibelight。

打包后的 exe 不走这里（PyInstaller 用自己的入口）。
本文件仅为开发期方便，例如：
    pyenv\\python.exe run.py set red --src claude
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from vibelight.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
