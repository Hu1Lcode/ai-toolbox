"""ZCode 日志监控 provider。

原理：
- ZCode 把所有运行时事件按天写进 JSONL 日志：
  ~/.zcode/cli/log/zcode-YYYY-MM-DD.jsonl
- 本 provider 在托盘后台线程里 tail 该日志的最新行，
  根据 event 字段推断 AI 状态并写入 state.json。

事件 → 状态映射（基于实际日志分析）：
  用户提交消息到模型开始流式响应之间           → red  (思考中)
  model.sdk.stream.completed + finishReason:
      - "end_turn"                             → green (本轮完成)
      - "tool-calls" / rawFinishReason "tool_use" → amber (有工具调用，可能待授权)
  tool.call.started / tool.approval.request    → amber (等待授权/执行中)
  tool.call.completed 且后续无新模型请求        → green (工具执行完)

实现策略（简化版，状态机只看最后一条决定性事件）：
- 维护“最后决定性事件”，遇到工具调用相关事件先置 amber，
  遇到 end_turn 的 stream.completed 置 green，否则 red。
- 用按行偏移量记录已读位置，避免重复处理。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from . import store

# ZCode 日志目录
def _zcode_log_dir() -> Path:
    return Path.home() / ".zcode" / "cli" / "log"


def _today_log() -> Path:
    """今天的日志文件路径。ZCode 跨日会切到新文件。"""
    today = datetime.now().strftime("%Y-%m-%d")
    return _zcode_log_dir() / f"zcode-{today}.jsonl"


def _classify(line: str) -> str | None:
    """解析一行 JSONL，返回应设置的状态，或 None（不决定状态）。

    基于 ZCode 实际日志事件（已用真实会话验证）：
      turn.started            → red   用户发了新消息，开始思考
      model.*.completed       → 不直接判定（见 stream.completed）
      tool.call.started       → amber 工具执行中
      tool.permission.resolved→ amber 权限已决断，即将执行工具
      model.sdk.stream.completed + finishReason:
          end_turn            → green 模型回复完毕
          tool-calls/tool_use → amber 模型要调工具
      turn.completed          → green 整轮彻底结束
    """
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    event = rec.get("event", "")
    ctx = rec.get("context", {}) or {}

    # 用户提交消息 / 新 turn 开始 → 立即红灯
    if event == "turn.started":
        return "red"

    # 工具相关 → 黄灯（执行中 / 待授权）
    if event in ("tool.call.started", "tool.approval.request",
                 "tool.permission.resolved"):
        return "amber"

    # 模型流式响应结束：按 finishReason 分流
    if event == "model.sdk.stream.completed":
        chunk = ctx.get("chunkCounts", {}) or {}
        if "tool-approval-request" in chunk:
            return "amber"
        finish = ctx.get("finishReason") or ctx.get("rawFinishReason") or ""
        if finish in ("tool-calls", "tool_use", "tool_use_stop"):
            return "amber"
        if finish in ("end_turn", "stop", "max_tokens"):
            return "green"
        return "amber"  # 未知 finish，保守按黄

    # 整轮结束 → 绿灯（最可靠的“完成”信号）
    if event == "turn.completed":
        return "green"

    return None


class ZCodeWatcher:
    """Tail ZCode JSONL 日志，把状态写进 state.json。"""

    def __init__(self, poll_interval: float = 0.5, agent: str = "zcode") -> None:
        self.poll_interval = poll_interval
        self.agent = agent
        self._stop = False
        self._fp = None
        self._inode_key = None  # (path, size) 用于检测文件被截断/切换
        self._idle_since: float | None = None  # 上次有活动的时间

    def _open_today(self) -> object | None:
        """打开今天的日志文件，跨日自动切换。返回文件对象或 None。"""
        path = _today_log()
        if not path.exists():
            return None
        # 若已打开且仍是同一文件，复用
        if self._fp is not None:
            try:
                cur = Path(self._fp.name).resolve()
            except Exception:
                cur = None
            if cur == path.resolve():
                return self._fp
            # 文件变了（跨日），关掉旧的
            try:
                self._fp.close()
            except Exception:
                pass
            self._fp = None
        try:
            self._fp = open(path, "r", encoding="utf-8", errors="replace")
            # 跳到末尾：启动时只处理“之后”的新事件，避免把历史状态当现状
            self._fp.seek(0, os.SEEK_END)
        except OSError:
            self._fp = None
        return self._fp

    def _read_new_lines(self) -> list[str]:
        fp = self._open_today()
        if fp is None:
            return []
        lines = []
        while True:
            line = fp.readline()
            if not line:
                break
            line = line.strip()
            if line:
                lines.append(line)
        return lines

    def _apply(self, state: str, detail: str = "") -> None:
        """写状态到 store（仅当与当前不同时才刷新 since）。"""
        try:
            data = store.load_state()
            cur = data.get("agents", {}).get(self.agent, {}).get("state")
            if cur == state:
                return  # 未变化，不写文件，减少抖动
            store.set_agent(self.agent, state, detail)
        except Exception as e:
            # watcher 绝不能因写状态失败而崩
            print(f"[zcode-watcher] set state error: {e}", flush=True)

    def step(self) -> None:
        """处理一轮：读新行 → 取最后一条决定性状态 → 写入。"""
        lines = self._read_new_lines()
        if not lines:
            return
        self._idle_since = time.time()
        last_state = None
        last_detail = ""
        for line in lines:
            s = _classify(line)
            if s:
                last_state = s
                last_detail = line  # 留全文太长，下面只取事件名
        if last_state:
            # detail 只取事件名，避免存巨长 JSON
            try:
                ev = json.loads(last_detail).get("event", "")
                last_detail = ev
            except Exception:
                last_detail = ""
            self._apply(last_state, last_detail)

    def run(self) -> None:
        """后台循环，直到 stop()。

        启动时会先回看日志尾部，确定“当前状态”，
        避免托盘刚启动时还停留在上一次离开前的旧状态。
        """
        # 回看：打开今天的日志，从末尾往前找最后一条决定性事件
        self._startup_recover()
        while not self._stop:
            try:
                self.step()
            except Exception as e:
                print(f"[zcode-watcher] step error: {e}", flush=True)
            time.sleep(self.poll_interval)
        # 退出时清理：把 zcode 置 idle，避免红灯一直亮着
        try:
            store.set_agent(self.agent, "idle", "watcher stopped")
        except Exception:
            pass

    def _startup_recover(self) -> None:
        """读今日日志尾部，应用最后一个决定性状态，然后把游标移到末尾。"""
        path = _today_log()
        if not path.exists():
            return
        try:
            # 读最后 200 行（足够覆盖一个完整 turn 的事件序列）
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-200:]
        except OSError:
            return
        last_state = None
        last_detail = ""
        for line in lines:
            line = line.strip()
            if not line:
                continue
            s = _classify(line)
            if s:
                last_state = s
                try:
                    last_detail = json.loads(line).get("event", "")
                except Exception:
                    last_detail = ""
        if last_state:
            self._apply(last_state, last_detail)
        # 游标移到末尾：重置已打开的 fp
        if self._fp is not None:
            try:
                self._fp.close()
            except Exception:
                pass
            self._fp = None

    def stop(self) -> None:
        self._stop = True
        if self._fp:
            try:
                self._fp.close()
            except Exception:
                pass


def main() -> int:
    """独立运行（调试用）：前台 tail 并打印推断状态。"""
    w = ZCodeWatcher(poll_interval=0.5)
    print(f"监控 ZCode 日志: {_today_log()}")
    print("等待事件... (Ctrl+C 退出)")
    try:
        w.run()
    except KeyboardInterrupt:
        w.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
