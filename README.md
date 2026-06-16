# VibeLight — AI Vibe Coding 状态指示灯 🔴🟡🟢

一个常驻 Windows 系统托盘的小工具，用 **红 / 黄 / 绿** 三色灯实时显示当前 vibe coding 过程中 AI 平台（Claude Code、ZCode 等）的工作状态。

| 灯色 | 状态 | 含义 |
| ---- | ---- | ---- |
| 🔴 红 | 思考中 | AI 正在处理请求，可放心离开 |
| 🟡 黄 | 需关注 | AI 触发了需授权/决策的事件，请回去确认 |
| 🟢 绿 | 已完成 | 本轮思考结束，结果已返回，该回去看对话了 |

> 设计详见 [`DESIGN.md`](./DESIGN.md)。本目录是该设计的 **可运行实现**。

---

## 它解决什么问题

AI 编码助手思考时往往几十秒到几分钟。期间你总在 AI 窗口和编辑器间反复切换看「好了没」，打断心流；AI 弹了授权请求或已完成，又常错过。

VibeLight 把这些状态压缩成一眼可见的托盘色块——**瞄一眼就知道现在该不该回去**。

## 工作原理（一句话）

各 AI 平台通过 **hook 脚本** 调用 `vibelight.exe set <color>`，把状态写进一个共享的 `state.json`；常驻的托盘守护进程监听该文件，切换图标并按需弹通知。多 agent 并发时按「最严重优先」聚合（amber > red > green）。

```
Claude Code ─┐
ZCode ────────┼─► vibelight set red/amber/green ─► state.json ─► 托盘图标 + 通知
（未来更多）─┘
```

## 快速开始

### 方式一：直接用打包好的 exe（推荐）

1. 到 [Releases](../../releases) 下载 `vibelight.exe`。
2. 双击运行（或放到任意目录），托盘出现 🔴🟡🟢 之一。
3. 安装 hooks（让 AI 平台自动上报状态）：
   ```bat
   python integrations\install_hooks.py --exe C:\path\to\vibelight.exe
   ```
   详见 [集成各平台](#集成各平台)。

### 方式二：从源码运行 / 自行打包

需要 Python 3.10+：

```bat
pip install pystray Pillow pyinstaller

:: 开发期直接跑
python run.py tray            :: 启动托盘
python run.py set red --src claude --detail thinking
python run.py status

:: 打包成 exe
pyinstaller vibelight.spec.py --noconfirm --clean
:: 产物在 dist\vibelight\vibelight.exe
```

## 命令行用法

| 命令 | 作用 |
| ---- | ---- |
| `vibelight` 或 `vibelight tray` | 启动托盘守护进程（默认） |
| `vibelight set <red\|amber\|green\|idle> --src <agent> [--detail ...]` | 写入某 agent 状态（**供 hook 调用**） |
| `vibelight status` | 打印当前所有 agent 状态与聚合结果 |
| `vibelight clear --src <agent>` | 移除某 agent 状态 |
| `vibelight icons` | 导出四态图标 PNG 到 `assets/icons` |

**状态文件位置**：`%APPDATA%\VibeLight\state.json`（Windows）/ `~/.vibelight/state.json`（macOS/Linux）。

## 集成各平台

### Claude Code

Claude Code 原生支持 hooks（`UserPromptSubmit` / `PreToolUse` / `Stop` / `Notification`）。一键安装：

```bat
python integrations\install_hooks.py --claude --exe C:\path\to\vibelight.exe
```

脚本会合并到 `~/.claude/settings.json`，幂等可重复执行。对应的事件映射：

| Claude 事件 | VibeLight 状态 | 含义 |
| ---- | ---- | ---- |
| `UserPromptSubmit` | 🔴 red | 用户提交了新 prompt，开始思考 |
| `PreToolUse` | 🟡 amber | 即将执行工具（写文件/命令等），待授权 |
| `Notification` | 🟡 amber | Claude 主动通知（如等输入） |
| `Stop` | 🟢 green | 本轮回复结束 |

### ZCode

```bat
python integrations\install_hooks.py --zcode --exe C:\path\to\vibelight.exe
```

（ZCode 的配置目录约定为 `~/.zcode/settings.json`，若实际不同请按其文档调整路径。）

### 其他平台（OpenCode / Codex / Aider）

这些平台暂无原生 hook。可：
- 监控其会话日志文件（`log_watch` provider，开发中）；
- 或退化为进程探测（`process_probe`，兜底）。

见 `DESIGN.md` 第 3 节的路线图。

## 状态机

```
提交 prompt / 继续
        │
        ▼
   ┌─────────┐  触发工具授权   ┌─────────┐
   │ 🔴 思考  │ ──────────────► │ 🟡 关注  │
   └────┬────┘                 └────┬────┘
        │ 正常产出完成              │ 用户处理完毕
        ▼                          ▼
   ┌─────────┐                 ┌─────────┐
   │ 🟢 完成  │ ◄────────────── │         │
   └─────────┘  下次提交         └─────────┘
```

## 目录结构

```
.
├── DESIGN.md                 设计文档
├── README.md                 本文件
├── vibelight.spec.py         PyInstaller 打包配置
├── run.py                    开发期启动器
├── src/vibelight/
│   ├── __main__.py           入口：子命令分发
│   ├── store.py              state.json 原子读写 + 聚合
│   ├── icons.py              程序内绘制四态图标
│   └── tray.py               系统托盘守护进程
├── integrations/
│   ├── claude/settings.hooks.json   Claude hooks 配置片段
│   └── install_hooks.py             一键安装/卸载脚本
└── assets/icons/            导出的图标 PNG
```

## 开发路线图（来自 DESIGN.md）

- [x] **Phase 0** 原型：状态协议 + hook 链路跑通
- [x] **Phase 1 MVP**：Claude Code/ZCode 集成、多 agent 聚合、桌面通知、Windows 打包
- [ ] **Phase 2** 扩展：OpenCode 日志监控、Codex/Aider 进程探测、macOS/Linux 适配
- [ ] **Phase 3** 增强：配置 GUI、远程查看、统计面板、MCP server 主动上报

## License

MIT，见 [`LICENSE`](./LICENSE)。
