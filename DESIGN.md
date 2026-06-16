# AI Vibe Coding 状态指示器（红绿灯）

> 一个常驻 PC 系统托盘的小工具，用红 / 黄 / 绿三色灯实时显示当前 vibe coding 过程中 AI 平台（Claude Code、OpenCode、Codex、ZCode 等）的工作状态。

---

## 1. 项目概述

### 1.1 要解决的问题

在 vibe coding 时，开发者经常在多个工具/窗口间切换：一边是 AI 编码助手（CLI 或 TUI），一边是编辑器/浏览器。AI 思考时往往需要几十秒甚至几分钟，期间：

- 反复切回 AI 窗口看「好了没」，打断心流；
- AI 已经返回结果或弹出了授权请求，却没及时看到；
- 多个 AI agent 并发跑时，根本不知道哪个该管了。

### 1.2 核心设想

借用红绿灯的直觉化隐喻，把 AI 状态压缩成一眼可见的色块：

| 灯色 | 状态         | 含义                                            |
| ---- | ------------ | ----------------------------------------------- |
| 🔴 红 | **思考中**   | AI 正在处理请求，尚未产出结果。用户可放心离开。  |
| 🟡 黄 | **需关注**   | AI 触发了需要用户决策/授权的事件（如工具调用确认）。 |
| 🟢 绿 | **已完成**   | AI 本轮思考结束，结果已返回，用户应回到对话。    |

把这三盏灯放进系统托盘，用户瞄一眼就知道现在该不该回去看 AI。

### 1.3 命名建议

可任选其一，或自定义：

- **VibeLight** —— 简洁直观（推荐）
- **AISignal** / **Codex Light** / **TrafficPrompt**
- 中文：**信号灯** / **编码红绿灯**

---

## 2. 状态模型

### 2.1 三态精确定义

```
        ┌──────────────────────────┐
        │  用户提交 prompt / 继续   │
        └─────────────┬────────────┘
                      ▼
               ┌────────────┐
               │  🔴 思考中  │  ◄──── 默认进入态
               └─────┬──────┘
   触发工具授权请求   │     │  正常产出完成
        ┌────────────┘     └──────────┐
        ▼                               ▼
 ┌────────────┐                   ┌────────────┐
 │  🟡 需关注  │ ──用户处理完毕──► │  🟢 已完成  │
 └────────────┘                   └─────┬──────┘
        ▲                               │
        └─────────────────────────────┘ 下次提交
```

### 2.2 状态机规则

- **🔴 → 🟡**：AI 请求执行某个需要授权的工具（写文件、执行命令、联网等），等待用户确认。
- **🔴 → 🟢**：AI 正常返回本轮回复（流结束 / Stop 事件触发）。
- **🟡 → 🔴**：用户批准授权后，AI 继续执行，回到思考态。
- **🟡 → 🟢**：用户拒绝授权，AI 给出最终回复后结束本轮。
- **🟢 → 🔴**：用户提交了下一次 prompt，进入新一轮思考。
- **超时降级**：🔴 持续超过 N 分钟（可配置，默认 10 分钟）可闪烁/变灰，提示可能卡死。

### 2.3 多 Agent 并发

当同时跑多个 AI 平台时（如一个 Claude Code 一个 ZCode），状态做「取最严重」聚合：

```
优先级：🟡（需关注） > 🔴（思考中） > 🟢（已完成） > ⚫（空闲/未启动）
```

托盘主图标显示聚合态；鼠标悬停或点击展开列表，看到每个 agent 各自的灯。

---

## 3. 状态检测策略（项目核心难点）

> 各 AI 平台并没有统一的状态 API，必须为每个平台设计「信号源」。整体策略按优先级采用以下四种方式，能 hook 就 hook，不能 hook 就盯日志/进程。

### 3.1 四种通用信号源

| 方式         | 原理                                         | 可靠性 | 适用场景                       |
| ------------ | -------------------------------------------- | ------ | ------------------------------ |
| **A. Hook**  | 平台支持事件钩子，钩子脚本把状态写入共享文件 | ★★★★★  | Claude Code、ZCode 等支持 hooks 的 |
| **B. 日志监控** | tail 平台的日志/会话文件，正则匹配关键事件   | ★★★    | OpenCode、有日志输出的 CLI      |
| **C. 进程探测** | 检测 CLI 进程是否存活、CPU 占用是否在工作    | ★★     | 兜底，只能粗略判断红/绿         |
| **D. 主动集成** | 提供一个轻量 CLI / MCP server，平台主动调用   | ★★★★★  | 长期目标，需要平台配合          |

### 3.2 各平台具体方案

#### Claude Code（Anthropic）
- **官方支持 Hooks**：`PreToolUse`、`PostToolUse`、`UserPromptSubmit`、`Stop`、`Notification`、`SubagentStop`。
- **接入方式**：在 `~/.claude/settings.json` 配置 hook 脚本，脚本调用本工具的 CLI 写状态：
  ```jsonc
  // ~/.claude/settings.json
  {
    "hooks": {
      "UserPromptSubmit": [{ "command": "vibelight set red  --src claude" }],
      "PreToolUse":       [{ "command": "vibelight set amber --src claude" }],
      "Stop":             [{ "command": "vibelight set green --src claude" }]
    }
  }
  ```
- 这是**最干净、最推荐**的接入路径。

#### ZCode
- 同样具备 hooks 机制（会话中 hook 输出被视为用户反馈），可复用上面的 hook 命令模式。
- 配置文件按 ZCode 文档约定写入。

#### OpenCode
- TUI 应用，无官方 hook。可监控其会话存储目录（通常在 `~/.local/share/opencode` 或 `~/.opencode`）下 JSONL 会话文件的增量更新。
- 通过匹配消息角色（`assistant` 正在写 / `user` 提交）推断状态。

#### Codex（OpenAI）/ Aider / 其他 CLI
- 多数为 CLI 进程，结合方式 B + C：
  - 监控其 stdout 日志文件（用户用 `tee` 重定向）；
  - 探测进程 CPU 占用是否处于「活跃思考」区间。
- 若工具支持 MCP 或插件，优先用方式 D 主动集成。

### 3.3 统一状态协议（建议固化）

无论哪种信号源，最终都收敛到一个**共享状态文件**（跨平台、跨进程）：

```jsonc
// 路径示例：Windows %APPDATA%\VibeLight\state.json
//         macOS/Linux ~/.vibelight/state.json
{
  "updated_at": "2026-06-16T10:23:45.120Z",
  "agents": {
    "claude":  { "state": "amber", "since": "2026-06-16T10:23:10Z", "detail": "PreToolUse: Write(...)" },
    "zcode":   { "state": "green", "since": "2026-06-16T10:20:01Z", "detail": "Stop" },
    "opencode": { "state": "red",  "since": "2026-06-16T10:23:40Z", "detail": "assistant streaming" }
  }
}
```

- 写入用**原子写**（写临时文件 → rename），避免读写竞争。
- 文件变更通过操作系统事件通知（Windows `ReadDirectoryChangesW` / macOS FSEvents / Linux inotify）触发托盘刷新，**不必轮询**。
- 协议文件可被任意工具读写，方便未来扩展（手机端、网页端都能看）。

---

## 4. 架构设计

### 4.1 分层

```
┌──────────────────────────────────────────────┐
│           UI 层（系统托盘 + 弹窗）            │  ◄── 用户可见
│   图标渲染 / tooltip / 右键菜单 / 详情面板    │
├──────────────────────────────────────────────┤
│            状态聚合与调度层                   │
│   多 agent 聚合 / 超时检测 / 通知触发         │
├──────────────────────────────────────────────┤
│            信号源适配层（Provider）           │
│  FileHookProvider │ LogWatchProvider │ ...   │
├──────────────────────────────────────────────┤
│            平台集成层                         │
│  Claude hooks │ ZCode hooks │ 日志监控配置    │
└──────────────────────────────────────────────┘
```

### 4.2 关键模块

- **Provider（信号源适配器）**：每个 AI 平台对应一个 Provider，负责把该平台的原始信号翻译成统一的 `{state, detail}` 更新，写入 state.json。
- **StateStore（状态中心）**：内存里维护聚合状态，监听 state.json 变化，计算聚合灯色，通知 UI。
- **TrayController（托盘控制）**：根据聚合状态切换图标、设置 tooltip、弹出系统通知。
- **CLI（vibelight）**：供 hook 脚本调用的轻量命令行入口，`vibelight set <color> --src <agent>`。

### 4.3 进程模型

推荐**双进程**：

1. **守护进程 `vibelight-daemon`**：常驻后台，监听 state.json，负责托盘图标与通知。开机自启。
2. **CLI `vibelight`**：由各平台 hook 瞬时调用，只做一件事——更新 state.json 后立即退出（开销 < 10ms）。

这样 hook 调用极轻，不阻塞 AI 工具本身。

---

## 5. 技术栈选型

| 候选              | 优点                              | 缺点                          | 推荐度 |
| ----------------- | --------------------------------- | ----------------------------- | ------ |
| **Rust + tray-icon + notify** | 单文件、跨平台、体积小（~5MB）、性能好 | 开发门槛略高                  | ⭐⭐⭐⭐⭐ |
| **Tauri**         | Rust 后端 + Web UI，可做漂亮面板  | 体积稍大、托盘依赖额外 crate  | ⭐⭐⭐⭐  |
| **Go + systray**  | 单文件、开发快、并发模型清晰      | 图标切换体验稍逊              | ⭐⭐⭐⭐  |
| **Electron**      | 生态成熟、UI 上手快               | 体积大（~80MB+）、资源占用高  | ⭐⭐    |
| **Python + pystray** | 原型最快                       | 打包分发麻烦、运行时依赖多    | ⭐⭐⭐（原型阶段） |

**建议路线**：
- **原型验证**用 Python + pystray，先把状态协议和 hook 链路跑通；
- **正式版**用 Rust（tray-icon + notify + serde）重写，产出单个 `.exe`，开机自启、无依赖。

---

## 6. 目录结构（参考）

```
vibelight/
├── DESIGN.md                 # 本文档
├── README.md
├── crates/
│   ├── core/                 # 状态模型、聚合逻辑（平台无关）
│   ├── store/                # state.json 读写 + 文件监听
│   ├── providers/            # 各平台信号源适配器
│   │   ├── file_hook.rs      # 通用 hook 状态文件
│   │   ├── log_watch.rs      # 日志监控
│   │   └── process_probe.rs  # 进程探测
│   ├── tray/                 # 系统托盘 UI（平台条件编译）
│   ├── cli/                  # vibelight 命令行（供 hook 调用）
│   └── daemon/               # 守护进程入口
├── integrations/             # 各平台 hook 配置示例与安装脚本
│   ├── claude/
│   │   ├── settings.patch.json
│   │   └── install.sh / install.ps1
│   ├── zcode/
│   ├── opencode/
│   └── codex/
├── assets/
│   └── icons/                # red.png amber.png green.png idle.png（多分辨率）
├── packaging/                # 安装包、开机自启配置
└── tests/
```

---

## 7. 配置文件示例

```jsonc
// %APPDATA%\VibeLight\config.json
{
  "version": 1,
  "language": "zh-CN",
  "agents": {
    "claude":   { "enabled": true,  "provider": "file_hook" },
    "zcode":    { "enabled": true,  "provider": "file_hook" },
    "opencode": { "enabled": true,  "provider": "log_watch",
                  "log_path": "~/.local/share/opencode/sessions.jsonl" },
    "codex":    { "enabled": false, "provider": "process_probe",
                  "process_name": "codex" }
  },
  "ui": {
    "aggregation": "worst_case",   // worst_case | per_agent
    "show_tooltip": true,
    "desktop_notification": true,
    "notify_on": ["amber", "green"]
  },
  "timeout": {
    "red_minutes_fade": 10         // 🔴 超过 10 分钟变灰提示
  }
}
```

---

## 8. 开发路线图

### Phase 0 — 原型验证（1～2 天）
- [ ] 用 Python 跑通最小链路：写一个脚本 `vibelight set red|amber|green`，改 state.json。
- [ ] pystray 读取 state.json，切换托盘图标（先用纯色 PNG）。
- [ ] 手工配置一个 Claude Code hook，端到端验证「思考→授权→完成」三态切换。

### Phase 1 — MVP（1～2 周）
- [ ] 完成 Claude Code 与 ZCode 的 hook 集成 + 一键安装脚本。
- [ ] 多 agent 聚合 + 详情面板（点击托盘看每个 agent）。
- [ ] 桌面通知（黄灯/绿灯触发）。
- [ ] Windows 打包出 `.exe` + 开机自启。

### Phase 2 — 平台扩展（2～3 周）
- [ ] OpenCode 日志监控 Provider。
- [ ] Codex / Aider 进程探测 Provider。
- [ ] macOS、Linux 托盘适配与打包。

### Phase 3 — 增强（按需）
- [ ] 配置 GUI（可视化勾选启用的平台）。
- [ ] 远程查看（手机/网页端读取同一 state.json，或走 WebSocket）。
- [ ] 统计面板：每个 agent 今日思考时长、授权次数。
- [ ] 暴露为 MCP server，让 AI 平台主动上报（方式 D）。

---

## 9. 风险与待解决问题

| 风险                                   | 影响 | 应对                                                        |
| -------------------------------------- | ---- | ----------------------------------------------------------- |
| 部分 AI 工具完全无 hook、无稳定日志     | 高   | 退化为进程探测；或推动其支持 MCP / 主动集成（Phase 3 方式 D） |
| 多平台同时写 state.json 的竞争         | 中   | 原子写 + 文件锁；CLI 写完即退出，窗口极短                     |
| 进程探测无法区分「思考中」与「空闲等待」| 中   | 仅作为兜底，优先用 hook / 日志                                |
| TUI 应用（如 OpenCode）全屏占用时遮挡托盘| 低   | 提供桌面通知与声音兜底                                        |
| 不同平台事件语义不一致（什么算「完成」）| 中   | 在 Provider 内归一化，对外只暴露红/黄/绿三态                  |

---

## 10. 开放讨论

以下几点建议在动手前先拍板，欢迎补充：

1. **首选平台**：先重点打磨哪个平台的集成？建议从 **Claude Code**（hook 最完善）起步。
2. **分发形态**：是否需要 macOS / Linux 版，还是先把 Windows 做透？
3. **图标风格**：纯色块 / 拟物红绿灯 / 极简线条？是否支持主题切换？
4. **是否开源 / 商业**：影响后续是否做远程查看、统计等「云」功能。

---

*本文档为初步设计，欢迎迭代。建议下一步：先把 Phase 0 原型（Python + 一个 Claude hook）跑起来，用真机验证状态协议是否顺手。*
