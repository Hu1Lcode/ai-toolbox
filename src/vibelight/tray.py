"""系统托盘 UI 后端（瘦身后）。

状态监测逻辑已抽到 engine.StatusEngine，本模块只负责：
1. 创建 pystray.Icon 和菜单。
2. 注册 engine.on_update 回调，把聚合状态反映到图标/tooltip。
3. 阻塞主线程跑托盘消息循环。

保留作为 `vibelight tray` 子命令的退路（桌面灯是默认模式）。
"""
from __future__ import annotations

import pystray

from . import icons, store
from .engine import StatusEngine, _fmt_detail
from .store import LABELS


class TrayApp:
    def __init__(self) -> None:
        self._engine = StatusEngine()
        self._icon: pystray.Icon | None = None

    # ---------- engine 回调 ----------
    def _on_update(self, agg: str, data: dict, tip: str) -> None:
        """engine 状态变化时调用：更新图标 + tooltip。"""
        if self._icon is not None:
            self._icon.icon = icons.make_icon(agg, size=64, frame=True)
            self._icon.title = tip

    # ---------- 菜单动作 ----------
    def _on_refresh(self, icon, item) -> None:
        self._engine.force_refresh()

    def _on_quit(self, icon, item) -> None:
        self._engine.stop()
        icon.stop()

    # ---------- 启动 ----------
    def run(self) -> None:
        # 先注册回调，再启动 engine（engine 会立刻读一次状态触发回调）
        self._engine.on_update(self._on_update)

        # 构建菜单：详情项动态展示各 agent 状态
        def detail_label():
            return _fmt_detail(store.load_state())

        menu = pystray.Menu(
            pystray.MenuItem(detail_label, None, enabled=False),
            pystray.MenuItem("刷新状态", self._on_refresh),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._on_quit),
        )

        # 初始图标用 idle，engine 回调很快会覆盖成真实状态
        self._icon = pystray.Icon(
            "vibelight",
            icons.make_icon("idle", size=64, frame=True),
            f"VibeLight — {LABELS['idle']}",
            menu,
        )

        # 启动 engine（watchers + 轮询线程），不阻塞
        self._engine.run_forever()

        # 阻塞主线程跑托盘
        self._icon.run()


def main() -> int:
    app = TrayApp()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
