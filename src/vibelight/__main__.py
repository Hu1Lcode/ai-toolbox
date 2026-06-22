"""VibeLight 主入口。

子命令：
- 无参 / desktop   : 启动桌面悬浮灯（默认，置顶圆形灯）
- tray             : 启动系统托盘守护进程（备选模式）
- set <state>      : 写入某个 agent 的状态（供 hook 调用，瞬时退出）
- status           : 打印当前所有 agent 状态
- clear <agent>    : 移除某个 agent 的状态
- icons            : 导出四态 PNG 图标到 assets/icons

示例（Claude Code hook 调用）：
    vibelight.exe set red --src claude --detail "thinking"
    vibelight.exe set amber --src claude --detail "PreToolUse: Write"
    vibelight.exe set green --src claude --detail "Stop"
"""
from __future__ import annotations

import argparse
import io
import os
import sys

# ── Windows GBK 终端安全处理 ──────────────────────────────────────
# 强制 stdout/stderr 使用 UTF-8 + replace，避免 emoji/中文在
# GBK 终端（Windows 默认代码页）下触发 UnicodeEncodeError。
if sys.platform == "win32":
    _enc = "utf-8"
    for _stream_name in ("stdout", "stderr"):
        _stream = getattr(sys, _stream_name, None)
        if _stream is not None and hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding=_enc, errors="replace")
            except Exception:
                pass

from . import store
from .store import LABELS


def _cmd_desktop(args) -> int:
    from . import desktop  # 延迟导入，避免 CLI 子命令也被拉起 GUI 依赖
    return desktop.main()


def _cmd_tray(args) -> int:
    from . import tray  # 延迟导入，避免 CLI 子命令也被拉起 GUI 依赖
    return tray.main()


def _cmd_set(args) -> int:
    try:
        data = store.set_agent(args.src, args.state, args.detail or "")
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    agg = store.aggregate(data)
    print(f"已设置 {args.src} -> {args.state}（聚合: {LABELS.get(agg, agg)}）")
    print(f"状态文件: {store.state_path()}")
    return 0


def _cmd_status(args) -> int:
    data = store.load_state()
    agg = store.aggregate(data)
    print(f"聚合状态: {LABELS.get(agg, agg)}")
    print(f"更新时间: {data.get('updated_at')}")
    agents = data.get("agents", {})
    if not agents:
        print("（尚无 agent 上报状态）")
        return 0
    print("各 agent:")
    for name in sorted(agents):
        info = agents[name]
        s = info.get("state", "idle")
        print(f"  • {name}: {LABELS.get(s, s)}  since={info.get('since')}  detail={info.get('detail','')}")
    return 0


def _cmd_clear(args) -> int:
    store.clear_agent(args.src)
    print(f"已清除 {args.src} 的状态")
    return 0


def _cmd_icons(args) -> int:
    from .icons import _COLORS  # noqa
    from . import icons
    import os
    out_dir = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "icons")
    os.makedirs(out_dir, exist_ok=True)
    for name in icons._COLORS:
        icons.make_icon(name).save(os.path.join(out_dir, f"{name}.png"))
    print(f"导出图标到 {os.path.abspath(out_dir)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vibelight",
        description="AI vibe coding 状态指示灯（红/黄/绿）",
    )
    sub = p.add_subparsers(dest="cmd")

    # desktop（默认）
    pd = sub.add_parser("desktop", help="启动桌面悬浮灯（默认）")
    pd.set_defaults(func=_cmd_desktop)

    # tray（备选模式）
    pt = sub.add_parser("tray", help="启动系统托盘守护进程（备选）")
    pt.set_defaults(func=_cmd_tray)

    # set
    ps = sub.add_parser("set", help="写入某 agent 状态（供 hook 调用）")
    ps.add_argument("state", choices=sorted(store.VALID_STATES), help="red|amber|green|idle")
    ps.add_argument("--src", required=True, help="agent 名称，如 claude / zcode / opencode")
    ps.add_argument("--detail", default="", help="可选详情，如触发的 hook 名")
    ps.set_defaults(func=_cmd_set)

    # status
    pst = sub.add_parser("status", help="打印当前状态")
    pst.set_defaults(func=_cmd_status)

    # clear
    pc = sub.add_parser("clear", help="移除某 agent 的状态")
    pc.add_argument("--src", required=True, help="agent 名称")
    pc.set_defaults(func=_cmd_clear)

    # icons
    pi = sub.add_parser("icons", help="导出四态图标 PNG")
    pi.set_defaults(func=_cmd_icons)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # 无子命令时默认启动 desktop（桌面悬浮灯，取代托盘作为默认模式）
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ["desktop"]
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
