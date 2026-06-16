"""系统托盘守护进程。

职责：
1. 监听 state.json 变化（轮询 mtime，跨平台无需额外依赖）。
2. 根据聚合状态切换托盘图标 + tooltip。
3. 状态变化时弹桌面通知（默认 amber/green 触发）。
4. 右键菜单：查看详情、刷新、退出。

设计原则：
- 守护进程本身不关心哪个 agent；它只读 state.json 的聚合结果。
- 写状态由各平台的 hook 脚本经 CLI 瞬时完成，互不阻塞。
"""
from __future__ import annotations

import os
import threading
import time

import pystray
from PIL import Image

from . import icons, store

# 状态中文标签
LABELS = {
    "red": "🔴 思考中",
    "amber": "🟡 需关注（待授权）",
    "green": "🟢 已完成",
    "idle": "⚫ 空闲",
}
# 触发桌面通知的状态
NOTIFY_ON = {"amber", "green"}
# 轮询间隔（秒）。state.json 很小，轮询 mtime 足够轻量
POLL_INTERVAL = 0.5
# 红灯超过该分钟数视为“可能卡死”，tooltip 提示
RED_FADE_MINUTES = 10


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _fmt_detail(data: dict) -> str:
    """生成多 agent 详情文本，供 tooltip 与菜单展示。"""
    agents = data.get("agents", {})
    if not agents:
        return "（尚无 agent 上报状态）"
    lines = []
    for name, info in sorted(agents.items()):
        state = info.get("state", "idle")
        label = LABELS.get(state, state)
        lines.append(f"  • {name}: {label}")
    return "\n".join(lines)


def _maybe_notify(state: str, prev: str, data: dict) -> None:
    """状态变化时尝试弹通知。失败静默（无通知后端不应崩掉托盘）。"""
    if state == prev or state not in NOTIFY_ON:
        return
    title = "VibeLight 状态更新"
    msg = {"amber": "AI 请求授权，请回去确认", "green": "AI 已完成本轮思考"}
    body = msg.get(state, state)
    # 附上哪些 agent 处于该状态
    names = [
        n for n, i in data.get("agents", {}).items() if i.get("state") == state
    ]
    if names:
        body = f"{body}\n来源: {', '.join(names)}"
    try:
        _os_notify(title, body)
    except Exception:
        pass  # 通知是锦上添花，绝不能因此让托盘崩溃


def _os_notify(title: str, body: str) -> None:
    """跨平台桌面通知。优先 Windows toast，退而求其次用 plyer（若可用）。"""
    if os.name == "nt":
        try:
            from ctypes import FormatError  # noqa: F401  仅探测可用性
        except Exception:
            pass
    try:
        import plyer  # 可选依赖
        plyer.notification.notify(title=title, message=body, app_name="VibeLight", timeout=5)
        return
    except Exception:
        pass
    # 退化：写一行到标准错误，至少留痕
    print(f"[notify] {title} - {body}", flush=True)


class TrayApp:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prev_state = None
        self._last_mtime = 0.0
        self._stop = threading.Event()
        self._icon: pystray.Icon | None = None

    # ---------- 状态读取 ----------
    def _refresh(self) -> None:
        """读 state.json，必要时更新图标/通知。线程安全。"""
        path = store.state_path()
        mt = _mtime(path)
        if mt == self._last_mtime:
            return  # 文件未变，跳过
        self._last_mtime = mt
        data = store.load_state()
        agg = store.aggregate(data)

        with self._lock:
            prev = self._prev_state
            self._prev_state = agg

        icon_img = icons.make_icon(agg)
        tip = f"VibeLight — {LABELS.get(agg, agg)}"
        # 红灯超时提示
        if agg == "red":
            for info in data.get("agents", {}).values():
                if info.get("state") == "red":
                    since = info.get("since")
                    if since:
                        try:
                            from datetime import datetime, timezone
                            dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
                            mins = (datetime.now(timezone.utc) - dt).total_seconds() / 60
                            if mins > RED_FADE_MINUTES:
                                tip += f"  ⚠ 已思考 {int(mins)} 分钟"
                        except Exception:
                            pass
                    break

        if self._icon is not None:
            self._icon.icon = icon_img
            self._icon.title = tip
        _maybe_notify(agg, prev, data)
        # 详情项动态文本
        detail = _fmt_detail(data)
        for item in getattr(self._icon, "_menu_items_detail", []):
            try:
                item.text = lambda: detail  # type: ignore[attr-defined]
            except Exception:
                pass

    # ---------- 后台轮询 ----------
    def _poll_loop(self) -> None:
        while not self._stop.wait(POLL_INTERVAL):
            try:
                self._refresh()
            except Exception as e:
                # 任何异常都不应让轮询线程死掉
                print(f"[poll] error: {e}", flush=True)

    # ---------- 菜单动作 ----------
    def _on_refresh(self, icon, item) -> None:
        self._last_mtime = 0.0
        self._refresh()

    def _on_quit(self, icon, item) -> None:
        self._stop.set()
        icon.stop()

    # ---------- 启动 ----------
    def run(self) -> None:
        # 初始图标：先强制读一次
        self._last_mtime = 0.0
        self._refresh()

        # 构建菜单。详情文本用一个可调用对象，实时展示各 agent 状态
        detail_text = ["loading..."]

        def detail_label():
            data = store.load_state()
            return _fmt_detail(data)

        menu = pystray.Menu(
            pystray.MenuItem(
                lambda _: detail_text[0],
                None,
                enabled=False,
            ),
            pystray.MenuItem("刷新状态", self._on_refresh),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._on_quit),
        )

        initial_state = self._prev_state or "idle"
        self._icon = pystray.Icon(
            "vibelight",
            icons.make_icon(initial_state),
            f"VibeLight — {LABELS.get(initial_state, initial_state)}",
            menu,
        )
        # 详情项改为动态：覆盖上面那个占位项的 text
        try:
            self._icon._menu._items[0].text = detail_label  # type: ignore[attr-defined]
        except Exception:
            pass

        # 启动后台轮询
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()

        # 阻塞运行托盘（主线程）
        self._icon.run()


def main() -> int:
    app = TrayApp()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
