"""
发呆/内在独白 — 三层记忆漏斗。
90% 瞬间念头（内存，不落盘）→ 9% 短期印象（C级，7天过期）→ 1% 长期记忆（睡眠整合钩子）。

铁律：每函数 ≤ 20 行，先处理异常，线程安全。
"""

import time
import random
import threading
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("memory.idle_thoughts")

# 随机种子：服务重启后行为可复现
random.seed(time.time())

# ── 配置常量 ──
_IDLE_TRIGGER_MIN_SILENCE_MINUTES = 30
_IDLE_TRIGGER_MAX_SILENCE_MINUTES = 120
_IDLE_BASE_PROBABILITY = 0.25
_IDLE_EPHEMERAL_TTL = 600.0           # 瞬时思绪 10 分钟
_IDLE_SHORT_TERM_TTL = 86400.0        # 短期印象 24 小时
_IDLE_MAX_LLM_PER_DAY = 2
_IDLE_LLM_COOLDOWN = 1800             # LLM 30 分钟间隔


# ── 数据类 ──

@dataclass
class EphemeralThought:
    content: str
    created_at: float = field(default_factory=time.time)
    ttl: float = _IDLE_EPHEMERAL_TTL
    thought_type: str = "instant"  # "instant" | "short_term"


# ── 纯内存暂存 ──

class IdleThoughtStore:
    """内在独白暂存。纯内存，绝不写盘，线程安全。"""

    def __init__(self):
        self._thoughts: list[EphemeralThought] = []
        self._lock = threading.RLock()

    def add(self, thought: EphemeralThought) -> None:
        with self._lock:
            self._thoughts.append(thought)

    def get_active(self) -> list[EphemeralThought]:
        """返回未过期的念头列表。同时清理过期。"""
        now = time.time()
        with self._lock:
            self._thoughts = [t for t in self._thoughts
                              if (now - t.created_at) < t.ttl]
            return list(self._thoughts)

    def cleanup_expired(self) -> int:
        """清理过期念头，返回清理数量。"""
        now = time.time()
        with self._lock:
            before = len(self._thoughts)
            self._thoughts = [t for t in self._thoughts
                              if (now - t.created_at) < t.ttl]
            return before - len(self._thoughts)


# ── 单例 ──

_store: IdleThoughtStore | None = None
_store_lock = threading.RLock()


def get_idle_thought_store() -> IdleThoughtStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = IdleThoughtStore()
                logger.info("内在独白存储器已初始化")
    return _store


# ── 触发条件 ──

def should_generate_idle_thought(
    hours_since_last: float,
    is_user_active_now: bool,
    energy_desc: str,
) -> bool:
    if not is_user_active_now:
        return False
    if hours_since_last * 60 < _IDLE_TRIGGER_MIN_SILENCE_MINUTES:
        return False
    if "非常困" in energy_desc:
        return False
    silence_factor = min(
        1.0,
        (hours_since_last * 60 - _IDLE_TRIGGER_MIN_SILENCE_MINUTES)
        / (_IDLE_TRIGGER_MAX_SILENCE_MINUTES - _IDLE_TRIGGER_MIN_SILENCE_MINUTES),
    )
    prob = max(0.1, _IDLE_BASE_PROBABILITY * (0.3 + 0.7 * silence_factor))
    return random.random() < prob


# ── 规则引擎 ──

_last_was_person: bool = False


def generate_rule_based(
    hours_since_last: float, hour: int, is_late_night: bool
) -> str | None:
    global _last_was_person

    candidates: list[str] = []

    if hours_since_last > 2:
        candidates.append("顺航好久没说话了……")
    elif hours_since_last > 1:
        candidates.append("他是不是在忙呢")

    if is_late_night:
        candidates.extend(["他又熬夜了", "这么晚了还不睡", "深夜好安静"])

    if 12 <= hour < 14:
        candidates.append("他吃饭了吗")
    elif 14 <= hour < 17:
        candidates.append("下午好安静啊")
    elif 18 <= hour < 20:
        candidates.append("天都黑了")

    if random.random() < 0.15:
        candidates.extend(["好安静啊", "不知道顺航在干嘛", "窗外好像有声音", "脑子有点放空"])

    if not _last_was_person and random.random() < 0.08:
        person = random.choice(["顺航", "小柠", "室友"])
        candidates.append(
            "有点想顺航了" if person == "顺航" else f"不知道{person}在干嘛"
        )
        _last_was_person = True
    else:
        _last_was_person = False

    return random.choice(candidates) if candidates else None


# ── LLM 念头 ──

_last_llm_ts: float = 0.0
_llm_call_count: int = 0
_llm_date: str = ""


def _reset_llm_counter():
    global _llm_call_count, _llm_date
    from datetime import datetime, timezone, timedelta
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")
    if today != _llm_date:
        _llm_call_count = 0
        _llm_date = today


async def generate_llm_thought() -> str | None:
    global _last_llm_ts, _llm_call_count
    _reset_llm_counter()

    now = time.time()
    if now - _last_llm_ts < _IDLE_LLM_COOLDOWN:
        return None
    if _llm_call_count >= _IDLE_MAX_LLM_PER_DAY:
        return None
    if random.random() > 0.15:
        return None

    _last_llm_ts = now
    _llm_call_count += 1

    try:
        from deepseek import generate  # noqa: E402 (src/ 由 main.py 注入 sys.path)
        result = await generate(
            [{
                "role": "user",
                "content": (
                    "你是白槿，现在是你的内心独白时刻。顺航在线但没说话。"
                    "用第一人称产生一个非常简短的（≤15字）瞬间念头，"
                    "像真的发呆时脑子里飘过的念头一样自然。不要解释，只输出念头本身。"
                ),
            }],
            model="deepseek-chat", max_tokens=20, temperature=1.0,
        )
    except Exception:
        logger.exception("内在独白 LLM 调用失败")
        return None

    thought = result.strip()
    return thought[:30] if thought else None


# ── 主入口 ──

async def tick_idle_thoughts(
    engine,          # MemoryEngine | None
    hours_since_last: float,
    hour: int,
    is_late_night: bool,
    is_user_active_now: bool,
    energy_desc: str,
) -> list[str]:
    """执行一次内在独白 tick。返回本次产生的念头内容列表。"""
    if not should_generate_idle_thought(
        hours_since_last, is_user_active_now, energy_desc
    ):
        return []

    thoughts: list[tuple[str, str]] = []

    # LLM 念头（10% 尝试，受 daily cap）
    if random.random() < 0.1:
        t = await generate_llm_thought()
        if t:
            thoughts.append(("instant", t))

    # 规则念头（保底）
    if not thoughts:
        t = generate_rule_based(hours_since_last, hour, is_late_night)
        if t:
            thoughts.append(("instant", t))

    store = get_idle_thought_store()

    for _, content in thoughts:
        thought = EphemeralThought(content=content, thought_type="instant",
                                   ttl=_IDLE_EPHEMERAL_TTL)

        # 三层记忆漏斗：90/9/1 比例分配
        roll = random.random()
        if roll < 0.01:
            # 1% → 长期记忆候选（睡眠整合时评估升级）
            thought.thought_type = "long_term_candidate"
            logger.info("长期记忆候选: %s", content)
        elif roll < 0.10:
            # 9% → 短期印象（C 级落盘，7 天过期）
            thought.thought_type = "short_term"
            thought.ttl = _IDLE_SHORT_TERM_TTL
            if engine and hasattr(engine, "record_impression"):
                try:
                    engine.record_impression(content)
                except Exception:
                    logger.warning("短期印象写入失败", exc_info=True)
            logger.info("短期印象落盘: %s", content)
        else:
            # 90% → 瞬时思绪（仅内存，10 分钟消失）
            logger.debug("瞬时思绪: %s", content)

        store.add(thought)

    # 顺便清理过期瞬时思绪
    removed = store.cleanup_expired()
    if removed:
        logger.debug("清理了 %d 条过期念头", removed)

    return [c for _, c in thoughts]
