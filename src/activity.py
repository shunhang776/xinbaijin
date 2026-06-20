"""
用户活跃时间学习：记录交互时间 → 识别活跃窗口 → 主动消息只在窗口内发送。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码。
"""

import json, logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("baijin.activity")
_ROOT = Path(__file__).parent.parent
_DATA_PATH = _ROOT / "data" / "activity.json"
_BEIJING = timezone(timedelta(hours=8))

_hour_buckets = {h: 0 for h in range(24)}
_last_interaction = None
_MIN_SAMPLES = 10


def _load():
    global _hour_buckets, _last_interaction
    try:
        if _DATA_PATH.exists():
            with open(_DATA_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
                _hour_buckets = {int(k): v for k, v in d.get("buckets", {}).items()}
                ts = d.get("last_interaction")
                if ts:
                    _last_interaction = datetime.fromisoformat(ts)
    except Exception:
        pass


def _save():
    try:
        _DATA_PATH.parent.mkdir(exist_ok=True)
        with open(_DATA_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "buckets": _hour_buckets,
                "last_interaction": _last_interaction.isoformat() if _last_interaction else None,
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"活跃数据保存失败: {e}")


def record():
    """记录一次用户交互。每次收到用户消息时调用。"""
    global _last_interaction
    now = datetime.now(_BEIJING)
    _hour_buckets[now.hour] = _hour_buckets.get(now.hour, 0) + 1
    _last_interaction = now
    _save()


def get_windows() -> list[tuple[int, int]]:
    """返回活跃时段列表。消息数超过均值 50% 的时段视为活跃。"""
    total = sum(_hour_buckets.values())
    if total < _MIN_SAMPLES:
        return [(7, 23)]
    avg = total / 24
    active = sorted([h for h, c in _hour_buckets.items() if c >= avg * 0.5])
    if not active:
        return [(7, 23)]
    windows = []
    start = end = active[0]
    for h in active[1:]:
        if h == end + 1:
            end = h
        else:
            windows.append((start, end))
            start = end = h
    windows.append((start, end))
    return windows


def is_active_now() -> bool:
    """当前是否在用户活跃时段内。"""
    h = datetime.now(_BEIJING).hour
    for s, e in get_windows():
        if s <= h <= e:
            return True
    return False


def hours_since_last() -> float:
    """距上次用户交互的小时数。无记录返回一个大数。"""
    if _last_interaction is None:
        return 999.0
    return (datetime.now(_BEIJING) - _last_interaction).total_seconds() / 3600


def should_greet() -> bool:
    """是否应该发送主动消息。活跃窗口内 + 超过 2h 没说话，或超过 3 天。"""
    if not is_active_now():
        return False
    h = hours_since_last()
    return h > 72 or h > 2


# 模块导入时加载历史数据
_load()
