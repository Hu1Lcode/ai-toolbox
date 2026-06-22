"""通用状态监测引擎 —— UI 无关。

把状态层和 UI 层解耦：
- StatusEngine 负责启动 provider watcher、轮询 state.json、聚合、通知。
- UI 后端（tray / desktop）通过 on_update(callback) 注册更新回调，
  回调签名: callback(agg: str, data: dict, tip: str)。
- engine.run_forever() 启动后台线程但不阻塞；UI 后端用自己的主循环阻塞。

这样托盘和桌面灯共享同一套状态/聚合/通知逻辑，互不干扰。
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

from . import store
from .store import LABELS

# 触发桌面通知的状态
NOTIFY_ON = {"amber", "green"}
# 轮询间隔（秒）。state.json 很小，高频轮询无压力；与 watcher 的写间隔
# 叠加决定端到端延迟（watcher 0.2s 写 + 本处 0.3s 读 ≈ 最坏 0.5s）。
POLL_INTERVAL = 0.3
# 红灯超过该分钟数视为“可能卡死”，tip 加提示
RED_FADE_MINUTES = 10


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _fmt_detail(data: dict) -> str:
    """把 data["agents"] 渲染成多行文本，供 UI 详情展示。"""
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
    """状态变化时尝试弹通知。失败静默（无通知后端不应崩掉引擎）。"""
    if state == prev or state not in NOTIFY_ON:
        return
    title = "VibeLight 状态更新"
    msg = {"amber": "AI 请求授权，请回去确认", "green": "AI 已完成本轮思考"}
    body = msg.get(state, state)
    names = [n for n, i in data.get("agents", {}).items() if i.get("state") == state]
    if names:
        body = f"{body}\n来源: {', '.join(names)}"
    try:
        _os_notify(title, body)
    except Exception:
        pass


def _os_notify(title: str, body: str) -> None:
    """跨平台桌面通知。优先 plyer（若可用），否则退化到 stderr。"""
    try:
        import plyer  # 可选依赖
        plyer.notification.notify(title=title, message=body, app_name="VibeLight", timeout=5)
        return
    except Exception:
        pass
    print(f"[notify] {title} - {body}", flush=True)


class StatusEngine:
    """状态监测引擎。UI 后端注册 on_update 回调后，调 run_forever() 启动。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prev_state: str | None = None
        self._last_mtime = 0.0
        self._stop = threading.Event()
        self._watchers: list = []
        self._on_update = None  # callback(agg, data, tip)

    # ---------- 回调注册 ----------
    def on_update(self, callback) -> None:
        """UI 后端注册更新回调。callback(agg, data, tip)。"""
        self._on_update = callback

    # ---------- watcher 启停 ----------
    def start_watchers(self) -> None:
        """启动所有 provider watcher 的后台线程。"""
        try:
            from .zcode_watcher import ZCodeWatcher
            zw = ZCodeWatcher(poll_interval=0.2)
            self._watchers.append(zw)
            threading.Thread(target=zw.run, daemon=True).start()
        except Exception as e:
            print(f"[engine] ZCode watcher 启动失败: {e}", flush=True)

    def stop(self) -> None:
        """停止所有 watcher 和轮询。"""
        self._stop.set()
        for w in self._watchers:
            try:
                w.stop()
            except Exception:
                pass

    # ---------- 状态读取 ----------
    def _refresh(self) -> None:
        """读 state.json，变化时触发 on_update 回调。线程安全。"""
        path = store.state_path()
        mt = _mtime(path)
        if mt == self._last_mtime:
            return
        self._last_mtime = mt
        data = store.load_state()
        agg = store.aggregate(data)

        with self._lock:
            prev = self._prev_state
            self._prev_state = agg

        # 构造 tip 文本（UI 无关，托盘/桌面灯都能用）
        tip = f"VibeLight — {LABELS.get(agg, agg)}"
        if agg == "red":
            for info in data.get("agents", {}).values():
                if info.get("state") == "red":
                    since = info.get("since")
                    if since:
                        try:
                            dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
                            mins = (datetime.now(timezone.utc) - dt).total_seconds() / 60
                            if mins > RED_FADE_MINUTES:
                                tip += f"  ⚠ 已思考 {int(mins)} 分钟"
                        except Exception:
                            pass
                    break

        _maybe_notify(agg, prev, data)

        if self._on_update is not None:
            try:
                self._on_update(agg, data, tip)
            except Exception as e:
                print(f"[engine] on_update 回调异常: {e}", flush=True)

    def force_refresh(self) -> None:
        """强制下次 _refresh 真正读文件（UI 手动刷新时用）。"""
        self._last_mtime = 0.0
        self._refresh()

    # ---------- 轮询 ----------
    def _poll_loop(self) -> None:
        while not self._stop.wait(POLL_INTERVAL):
            try:
                self._refresh()
            except Exception as e:
                print(f"[engine] poll error: {e}", flush=True)

    # ---------- 启动 ----------
    def run_forever(self) -> None:
        """启动 watchers + 轮询线程。不阻塞 —— 由 UI 后端的主循环阻塞。"""
        self.start_watchers()
        # 先读一次拿到初始状态
        self._last_mtime = 0.0
        self._refresh()
        # 启动后台轮询
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()
