"""状态存储：跨进程、跨平台的 state.json 读写。

协议（见 DESIGN.md 第 3.3 节）：
{
  "updated_at": "ISO8601",
  "agents": {
    "<agent>": {"state": "red|amber|green|idle", "since": "ISO8601", "detail": "..."}
  }
}

- 写入用原子写（临时文件 + os.replace），避免读写竞争。
- 聚合规则（worst_case）：amber > red > green > idle。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

# 状态优先级：数字越大越“严重”，聚合时取最大值
PRIORITY = {"idle": 0, "green": 1, "red": 2, "amber": 3}
VALID_STATES = set(PRIORITY)

STATE_FILENAME = "state.json"
CONFIG_FILENAME = "config.json"


def app_dir() -> str:
    """返回应用数据目录，不存在则创建。跨平台。"""
    if os.name == "nt":  # Windows: %APPDATA%\VibeLight
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "VibeLight")
    else:  # macOS / Linux: ~/.vibelight
        path = os.path.join(os.path.expanduser("~"), ".vibelight")
    os.makedirs(path, exist_ok=True)
    return path


def state_path() -> str:
    return os.path.join(app_dir(), STATE_FILENAME)


def config_path() -> str:
    return os.path.join(app_dir(), CONFIG_FILENAME)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def load_state() -> dict:
    """读取整份状态。文件缺失或损坏时返回空结构。"""
    empty = {"updated_at": None, "agents": {}}
    path = state_path()
    if not os.path.exists(path):
        return empty
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "agents" not in data:
            return empty
        return data
    except (json.JSONDecodeError, OSError):
        return empty


def save_state(data: dict) -> None:
    """原子写入整份状态。"""
    os.makedirs(os.path.dirname(state_path()), exist_ok=True)
    data["updated_at"] = now_iso()
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(state_path()), suffix=".tmp", prefix="state_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, state_path())  # 原子替换
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def set_agent(agent: str, state: str, detail: str = "") -> dict:
    """更新单个 agent 的状态并持久化，返回写后的整份状态。"""
    if state not in VALID_STATES:
        raise ValueError(f"非法状态 {state!r}，允许: {sorted(VALID_STATES)}")
    data = load_state()
    prev = data["agents"].get(agent, {})
    # 状态未变且未提供新 detail 时，不刷新 since（避免无谓的时间更新）
    if prev.get("state") == state and not detail:
        prev["since"] = prev.get("since", now_iso())
    else:
        prev["since"] = now_iso()
    prev["state"] = state
    if detail:
        prev["detail"] = detail
    data["agents"][agent] = prev
    save_state(data)
    return data


def aggregate(data: dict) -> str:
    """worst_case 聚合：取所有 agent 中最严重的状态。"""
    agents = data.get("agents", {})
    if not agents:
        return "idle"
    best = "idle"
    for info in agents.values():
        s = info.get("state", "idle")
        if PRIORITY.get(s, 0) > PRIORITY.get(best, 0):
            best = s
    return best


def clear_agent(agent: str) -> dict:
    """移除某个 agent（用于进程退出时清理）。"""
    data = load_state()
    data["agents"].pop(agent, None)
    save_state(data)
    return data
