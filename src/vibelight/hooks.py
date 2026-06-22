"""一键安装/卸载 VibeLight hooks 到 Claude Code（内置子命令的实现）。

这是 `vibelight install-hooks` 子命令的后端。把它内置进 exe 后，
Claude Code 用户下单个 vibelight.exe 就能配 hook，无需 Python、无需 clone 源码。

用法（由 __main__.py 转发）：
    vibelight install-hooks                  # 默认装 Claude Code（ZCode 靠内置日志监控，不需要 hook）
    vibelight install-hooks --claude         # 只装 Claude Code
    vibelight install-hooks --zcode          # 只装 ZCode（实验性，通常不需要）
    vibelight install-hooks --exe PATH       # 指定 vibelight.exe 路径（默认用自己）
    vibelight install-hooks --uninstall      # 移除已安装的 hooks

工作原理：
1. 定位 vibelight.exe（打包环境用 sys.executable 即自身；开发环境找 dist/vibelight.exe）。
2. 找到目标平台的 settings.json（Claude: ~/.claude/settings.json）。
3. 把 hooks 片段合并进去（已存在则先移除旧的同标记项，幂等）。
4. 写入一个备份文件 settings.json.vibelight.bak（仅在原文件存在时）。

与 integrations/install_hooks.py 的关系：
    本模块是权威实现；integrations/install_hooks.py 保留为源码运行场景的备用，
    两者逻辑保持一致，常量 HOOK_DEFS / MARKER 必须同步。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

# 各平台的 hooks 配置：事件名 -> 命令模板
# 与 integrations/install_hooks.py 的 HOOK_DEFS 保持一致
HOOK_DEFS = {
    "UserPromptSubmit": '{exe} set red --src {src} --detail UserPromptSubmit',
    "PreToolUse":       '{exe} set amber --src {src} --detail PreToolUse',
    "Stop":             '{exe} set green --src {src} --detail Stop',
    "Notification":     '{exe} set amber --src {src} --detail Notification',
}

# 标记位，用于识别本工具注入的 hook，便于幂等/卸载
# 与 integrations/install_hooks.py 的 MARKER 保持一致
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


def _resolve_exe(explicit: str | None) -> str:
    """定位 vibelight.exe 路径。

    优先级：
    1. 显式 --exe 参数
    2. PyInstaller 打包环境（sys.frozen）-> sys.executable（即 vibelight.exe 自身）
    3. 开发环境：从本文件位置往上找 dist/vibelight.exe
    4. 都找不到 -> 报错退出，提示用 --exe
    """
    if explicit:
        p = Path(explicit)
        if not p.exists():
            _exit(f"指定的 exe 不存在: {explicit}")
        return str(p.resolve())

    # 打包环境：自己就是 vibelight.exe
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve())

    # 开发环境：从 src/vibelight/hooks.py 往上找 ../dist/vibelight.exe
    here = Path(__file__).resolve().parent  # src/vibelight/
    candidates = [
        here.parent.parent / "dist" / "vibelight.exe",  # <repo>/dist/vibelight.exe
        here.parent / "vibelight.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c.resolve())

    _exit(
        "未找到 vibelight.exe。打包后运行时本会用自身路径；开发运行请用 --exe <路径> 指定，"
        f"或先打包：pyinstaller vibelight.spec --noconfirm --clean。已尝试: {[str(c) for c in candidates]}"
    )


def _exit(msg: str) -> None:
    """统一退出方式（打印到 stderr 后 SystemExit）。"""
    print(f"错误: {msg}", file=sys.stderr)
    raise SystemExit(2)


def run(platforms: list[str], exe: str | None, uninstall: bool) -> int:
    """入口：安装或卸载指定平台的 hooks。返回退出码。"""
    if uninstall:
        for p in platforms:
            _uninstall_platform(p)
        print("\n卸载完成。")
        return 0

    exe_path = _resolve_exe(exe)
    print(f"使用 vibelight: {exe_path}")
    for p in platforms:
        _install_platform(p, exe_path)
    print("\n安装完成。现在双击 vibelight.exe 启动桌面灯即可（Claude Code 状态会自动上报）。")
    return 0
