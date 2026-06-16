#!/usr/bin/env python3
"""一键安装 VibeLight hooks 到 Claude Code / ZCode。

用法：
    python install_hooks.py                 # 自动探测并安装到所有已发现的支持平台
    python install_hooks.py --claude        # 只装 Claude Code
    python install_hooks.py --zcode         # 只装 ZCode
    python install_hooks.py --exe PATH      # 指定 vibelight.exe 路径
    python install_hooks.py --uninstall     # 移除已安装的 hooks

工作原理：
1. 定位 vibelight.exe（默认与打包产物同级，或用 --exe 指定）。
2. 找到目标平台的 settings.json（Claude: ~/.claude/settings.json）。
3. 把 hooks 片段合并进去（已存在则跳过，幂等）。
4. 写入一个备份文件 settings.json.vibelight.bak。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# 各平台的 hooks 配置：事件名 -> 命令模板
HOOK_DEFS = {
    "UserPromptSubmit": '{exe} set red --src {src} --detail UserPromptSubmit',
    "PreToolUse":       '{exe} set amber --src {src} --detail PreToolUse',
    "Stop":             '{exe} set green --src {src} --detail Stop',
    "Notification":     '{exe} set amber --src {src} --detail Notification',
}

# 标记位，用于识别本工具注入的 hook，便于幂等/卸载
MARKER = "vibelight"


def _settings_path(platform: str) -> Path | None:
    """返回目标平台 settings.json 的路径，目录不存在则返回 None。"""
    home = Path.home()
    if platform == "claude":
        p = home / ".claude" / "settings.json"
    elif platform == "zcode":
        # ZCode 的配置目录约定（按常见实现；不存在则跳过）
        p = home / ".zcode" / "settings.json"
    else:
        return None
    return p if p.parent.exists() else None


def _build_hooks(exe: str, src: str) -> dict:
    """构造 Claude/ZCode 风格的 hooks 字典。"""
    hooks = {}
    for event, tmpl in HOOK_DEFS.items():
        cmd = tmpl.format(exe=exe, src=src)
        hooks[event] = [{"hooks": [{"type": "command", "command": cmd}]}]
    return hooks


def _install_platform(platform: str, exe: str) -> bool:
    path = _settings_path(platform)
    if path is None:
        print(f"[{platform}] 配置目录未找到，跳过（未安装该平台 CLI？）")
        return False
    src = platform  # agent 名称就用平台名

    # 读现有配置
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"[{platform}] {path} 不是合法 JSON，已备份为 .bak 后重写")
            shutil.copy2(path, str(path) + ".vibelight.bak")
            data = {}
    else:
        data = {}

    existing_hooks = data.get("hooks", {})
    new_hooks = _build_hooks(exe, src)

    # 幂等：若已有带 marker 的命令，先移除旧的
    for event in list(existing_hooks):
        entries = existing_hooks[event]
        if not isinstance(entries, list):
            continue
        kept = []
        for entry in entries:
            inner = entry.get("hooks", []) if isinstance(entry, dict) else []
            has_ours = any(
                MARKER in str(h.get("command", "")) for h in inner if isinstance(h, dict)
            )
            if not has_ours:
                kept.append(entry)
        if kept:
            existing_hooks[event] = kept
        else:
            existing_hooks.pop(event)

    # 合并新的
    existing_hooks.update(new_hooks)
    data["hooks"] = existing_hooks

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{platform}] 已写入 hooks -> {path}")
    return True


def _uninstall_platform(platform: str) -> bool:
    path = _settings_path(platform)
    if path is None or not path.exists():
        print(f"[{platform}] 未找到 {path}，跳过")
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"[{platform}] {path} 非合法 JSON，未改动")
        return False
    hooks = data.get("hooks", {})
    removed = 0
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept = []
        for entry in entries:
            inner = entry.get("hooks", []) if isinstance(entry, dict) else []
            has_ours = any(
                MARKER in str(h.get("command", "")) for h in inner if isinstance(h, dict)
            )
            if has_ours:
                removed += 1
            else:
                kept.append(entry)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{platform}] 移除了 {removed} 条 hooks -> {path}")
    return True


def _locate_exe(explicit: str | None) -> str:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            sys.exit(f"指定的 exe 不存在: {explicit}")
        return str(p.resolve())
    # 默认：与 install_hooks.py 相对的 ../dist/vibelight/vibelight.exe
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "dist" / "vibelight" / "vibelight.exe",
        here.parent / "dist" / "vibelight.exe",
        here.parent / "vibelight.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c.resolve())
    sys.exit(
        "未找到 vibelight.exe。请先运行打包，或用 --exe <路径> 指定。\n"
        f"已尝试: {[str(c) for c in candidates]}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="安装/卸载 VibeLight hooks")
    ap.add_argument("--claude", action="store_true", help="只装 Claude Code")
    ap.add_argument("--zcode", action="store_true", help="只装 ZCode")
    ap.add_argument("--exe", default=None, help="指定 vibelight.exe 路径")
    ap.add_argument("--uninstall", action="store_true", help="移除已安装的 hooks")
    args = ap.parse_args()

    platforms = []
    if args.claude:
        platforms.append("claude")
    if args.zcode:
        platforms.append("zcode")
    if not platforms:
        platforms = ["claude", "zcode"]

    if args.uninstall:
        for p in platforms:
            _uninstall_platform(p)
        return 0

    exe = _locate_exe(args.exe)
    print(f"使用 vibelight: {exe}")
    for p in platforms:
        _install_platform(p, exe)
    print("\n完成。现在启动托盘守护进程：双击 vibelight.exe（或运行 vibelight tray）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
