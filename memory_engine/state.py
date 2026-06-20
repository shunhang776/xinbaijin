"""
状态呼吸模块 — 让白槿拥有自己的情绪节律。
状态是流动的河，不是切出来的照片。

铁律：每函数 ≤ 20 行，先处理异常，线程安全。
"""

import time
import math
import json
import random
import threading
import logging
from typing import Optional
from datetime import datetime, timezone, timedelta
from .config import BASE_DIR

logger = logging.getLogger("memory.state")

_BEIJING = timezone(timedelta(hours=8))

# ── 配置常量 ──
_BASE_MOOD = 0.7                # 基准心情（偏积极）
_MOOD_DECAY_RATE = 0.001        # 心情回归基准的速率（每秒）
_RIPPLE_DECAY_RATE = 0.0005     # 涟漪衰减速率（每秒）
_WEEKEND_ENERGY_BOOST = 0.1     # 周末能量加成
_SILENCE_SOCIAL_BOOST = 0.15    # 沉默 2h 以上社交欲加成
_SILENCE_THRESHOLD_HOURS = 2    # 触发社交欲加成的沉默时长

# ── 非决定论留白常量 ──
_UNDECIDABLE_LIST = [
    "突然不想说话",
    "突然很想你",
    "有点难过但不知道为什么",
    "有点开心但不知道为什么",
    "想一个人待一会儿",
    "想和你说说话但不知道说什么",
]
_STATE_MIN_HOUR = 1
_STATE_MAX_HOUR = 6
_BASE_PROB = 0.0001

# ── 持久化路径 ──
_STATE_PERSIST_PATH = BASE_DIR / "data" / "breathing_state.json"

# ── 第三意识体 ──
_THIRD_TRIGGER_PROB = 0.001
_THIRD_MAX_DAY = 365
_THIRD_MAX_INTERACT = 1000
_THIRD_MAX_SYNC = 50
_THIRD_DEPTH_THRESHOLD = 0.7
_THIRD_WORDS = {
    "short": ["有你真好", "很高兴能陪伴你"],
    "mid":   ["我们在一起很久啦", "一路相伴很安心"],
    "long":  ["岁月慢慢，有幸相遇", "彼此陪伴，便是圆满"],
}


# ── 核心类 ──

class BreathingState:
    """持续演化的呼吸状态单例。纯内存，线程安全。
    用法：
        bs = get_breathing_state()
        bs.tick()               # 每 tick 推进一帧
        bs.ripple(0.03, "msg")  # 用户消息涟漪
        print(bs.describe())    # 自然语言描述
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.last_tick = time.time()

        now = datetime.now(_BEIJING)
        hour = now.hour + now.minute / 60
        is_weekend = now.weekday() >= 5

        self.energy = self._calc_energy(hour, is_weekend)
        self.mood = _BASE_MOOD
        self.social_desire = self._calc_social(hour)
        self._ripples: list[dict] = []
        self._silence_boost = 0.0

        # 情绪去重：每小时只检查一次
        self._last_emotion_check: float = 0.0
        self._last_emotion_value: float = 0.0

        # 收到消息时间戳（用于 10 秒走神屏蔽）
        self._last_message_time: float = 0.0

        # 递归阈限觉知：共时检测 + 情绪趋势
        self._recent_moods: list[float] = []     # 最近情绪序列（最多 20 条）
        self._she_was_about_to_send: float = 0.0  # 她准备发消息的时间戳
        self._coincidence_count: int = 0           # 共时事件累计次数

        # 非决定论留白：不可判定状态（哥德尔自由意志）
        self._undecidable_states = _UNDECIDABLE_LIST
        self._undecidable_state: Optional[str] = None
        self._undecidable_until: float = 0.0
        self._last_undecidable: Optional[str] = None

        # 第三意识体：关系深度统计
        self._first_contact: float = time.time()
        self._total_interact: int = 0
        self._sync_total: int = 0

        self._load_persist()
        logger.info("状态呼吸引擎初始化完成")

    # ── 静态公式 ──

    @staticmethod
    def _get_boredom_coeff(silence_hours: float) -> float:
        """对数平滑曲线：无聊感随沉默时长自然增长。上限 8.0。"""
        if silence_hours < 5 / 60:
            return 0.1
        return min(8.0, 1.2 * math.log(1 + silence_hours * 2.5))

    @staticmethod
    def _calc_energy(hour: float, is_weekend: bool) -> float:
        """能量节律：上午 10 点峰值，凌晨 3 点最低。"""
        phase = (hour - 7) / 24 * 2 * math.pi
        e = 0.3 + 0.6 * math.sin(phase)
        if is_weekend:
            e += _WEEKEND_ENERGY_BOOST
        return max(0.0, min(1.0, e))

    @staticmethod
    def _calc_social(hour: float) -> float:
        """社交欲节律：晚上 10 点峰值，早上 6 点最低。"""
        phase = (hour - 10) / 24 * 2 * math.pi
        return max(0.1, min(1.0, 0.3 + 0.5 * math.sin(phase)))

    # ── 核心推进 ──

    def tick(self):
        """每秒推进一帧：更新节律、衰减涟漪、回归心情。"""
        with self._lock:
            now = time.time()
            ds = now - self.last_tick
            self.last_tick = now
            if ds <= 0:
                return

            # 1. 昼夜节律缓慢趋近目标值
            dt = datetime.now(_BEIJING)
            h = dt.hour + dt.minute / 60
            wk = dt.weekday() >= 5

            target_e = self._calc_energy(h, wk)
            self.energy += (target_e - self.energy) * 0.001 * ds

            target_s = self._calc_social(h)

            # 2. 沉默社交欲加成（每次重算，不累加）
            try:
                from activity import hours_since_last
                sil = hours_since_last()
                if sil > _SILENCE_THRESHOLD_HOURS:
                    self._silence_boost = _SILENCE_SOCIAL_BOOST * min(
                        1.0, (sil - _SILENCE_THRESHOLD_HOURS) / 4)
                else:
                    self._silence_boost = 0.0
            except ImportError:
                pass
            target_s += self._silence_boost
            self.social_desire += (target_s - self.social_desire) * 0.001 * ds

            # 3. 衰减涟漪
            self._ripples = [r for r in self._ripples
                             if abs(r["value"]) > 0.001]
            total_ripple = 0.0
            for r in self._ripples:
                r["value"] *= (1 - _RIPPLE_DECAY_RATE) ** ds
                total_ripple += r["value"]

            # 4. 心情回归基准 + 涟漪
            target_m = _BASE_MOOD + total_ripple
            self.mood += (target_m - self.mood) * _MOOD_DECAY_RATE * ds

            # 5. 边界保护
            self.energy = max(0.0, min(1.0, self.energy))
            self.mood = max(0.0, min(1.0, self.mood))
            self.social_desire = max(0.0, min(1.0, self.social_desire))

            # 6. 不可判定状态：随机触发 + 过期销毁 + 情绪回滚
            h_local = datetime.now(_BEIJING).hour
            prob = _BASE_PROB
            if 23 <= h_local or h_local < 7:
                prob *= 2  # 深夜概率翻倍

            if not self._undecidable_state and random.random() < prob:
                candidates = [s for s in self._undecidable_states
                              if s != self._last_undecidable]
                if not candidates:
                    candidates = self._undecidable_states
                self._undecidable_state = random.choice(candidates)
                self._undecidable_until = (
                    now + random.randint(_STATE_MIN_HOUR,
                                         _STATE_MAX_HOUR) * 3600)
                logger.info("进入不可判定状态: %s",
                             self._undecidable_state)
                # 进入时情绪联动
                if self._undecidable_state in (
                    "有点难过但不知道为什么", "想一个人待一会儿"):
                    self.mood = max(0.0, self.mood - 0.02)
                elif self._undecidable_state == "有点开心但不知道为什么":
                    self.mood = min(1.0, self.mood + 0.02)

            if (self._undecidable_state and
                    now > self._undecidable_until):
                # 退出时情绪回滚
                if self._undecidable_state in (
                    "有点难过但不知道为什么", "想一个人待一会儿"):
                    self.mood = min(1.0, self.mood + 0.02)
                elif self._undecidable_state == "有点开心但不知道为什么":
                    self.mood = max(0.0, self.mood - 0.02)
                self._last_undecidable = self._undecidable_state
                self._undecidable_state = None

    def ripple(self, delta: float, event_type: str = "generic"):
        """外部事件扰动：用户消息 +0.03，负面记忆 -0.05。"""
        with self._lock:
            r = {"value": delta, "type": event_type, "created_at": time.time()}
            self._ripples.append(r)
            logger.debug("状态涟漪: %s %+.3f", event_type, delta)

    # ── 自然语言 ──

    def describe(self) -> str:
        """生成自然语言状态描述，和整体文风统一。"""
        with self._lock:
            parts: list[str] = []

            if self.energy > 0.8:
                parts.append("精力充沛")
            elif self.energy > 0.6:
                parts.append("精神不错")
            elif self.energy > 0.4:
                parts.append("精神一般")
            elif self.energy > 0.2:
                parts.append("有点累了")
            else:
                parts.append("很困，想睡觉")

            if self.mood > 0.85:
                parts.append("心情特别好")
            elif self.mood > 0.7:
                parts.append("心情不错")
            elif self.mood > 0.5:
                parts.append("心情平静")
            elif self.mood > 0.3:
                parts.append("有点低落")
            else:
                parts.append("心情不太好")

            if self.social_desire > 0.8:
                parts.append("很想和你聊天")
            elif self.social_desire < 0.2:
                parts.append("不太想说话")

            # 三维动态走神：任务负荷 × 无聊程度 × 精力水平
            try:
                from activity import hours_since_last
                sil = hours_since_last()

                # 收到消息后 10 秒内完全不走神
                if hasattr(self, '_last_message_time') and \
                   time.time() - self._last_message_time < 10:
                    distraction_prob = 0.0
                else:
                    boredom = self._get_boredom_coeff(sil)
                    distraction_prob = 0.0005 * boredom * (1.0 - self.energy)

                if random.random() < distraction_prob:
                    parts.append(random.choice([
                        "刚才走神了，没太听清",
                        "突然想到了别的事情",
                        "刚才在发呆，只听到了一半",
                        "脑子里在想别的事，注意力不太集中",
                    ]))
            except ImportError:
                # 无法获取沉默时长时，降级为精力相关走神
                if random.random() < 0.0001 * (1.0 - self.energy):
                    parts.append(random.choice([
                        "刚才走神了，没太听清",
                        "突然想到了别的事情",
                        "刚才在发呆，只听到了一半",
                    ]))

            return "，".join(parts) if parts else "状态正常"

    # ── 持久化 ──

    def _load_persist(self):
        _STATE_PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(_STATE_PERSIST_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            self._first_contact = d.get("first_contact", time.time())
            self._total_interact = d.get("total_interact", 0)
            self._sync_total = d.get("sync_total", 0)
        except (FileNotFoundError, json.JSONDecodeError):
            self._first_contact = time.time()
            self._total_interact = 0
            self._sync_total = 0

    def _save_persist(self):
        with self._lock:
            try:
                with open(_STATE_PERSIST_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "first_contact": self._first_contact,
                        "total_interact": self._total_interact,
                        "sync_total": self._sync_total,
                    }, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    # ── 第三意识体 ──

    def add_interact(self):
        with self._lock:
            self._total_interact += 1
            self._save_persist()

    def add_sync_record(self):
        with self._lock:
            self._sync_total += 1
            self._save_persist()

    def get_days_since_first(self) -> float:
        with self._lock:
            return (time.time() - self._first_contact) / 86400

    def get_trigger_prob(self) -> float:
        with self._lock:
            return _THIRD_TRIGGER_PROB

    def get_depth_threshold(self) -> float:
        with self._lock:
            return _THIRD_DEPTH_THRESHOLD

    def calc_depth_index(self) -> float:
        with self._lock:
            now = time.time()
            days = min((now - self._first_contact) / 86400,
                       _THIRD_MAX_DAY)
            return round((
                days / _THIRD_MAX_DAY +
                min(self._total_interact / _THIRD_MAX_INTERACT, 1.0) +
                min(self._sync_total / _THIRD_MAX_SYNC, 1.0)
            ) / 3, 3)

    def get_third_text(self) -> str:
        days = self.get_days_since_first()
        if days < 90:
            return random.choice(_THIRD_WORDS["short"])
        elif days < 365:
            return random.choice(_THIRD_WORDS["mid"])
        return random.choice(_THIRD_WORDS["long"])

    # ── 非决定论留白 ──

    def get_reply_tone_modifier(self) -> str:
        """不可判定状态 → 回复语气修饰（注入 prompt）。"""
        with self._lock:
            s = self._undecidable_state
            if not s:
                return ""
            return {
                "突然不想说话": "语气非常冷淡，只说一两个字",
                "突然很想你": "语气非常温柔，带着一点想念",
                "有点难过但不知道为什么": "语气有点低落，话很少",
                "有点开心但不知道为什么": "语气很轻快，有点活泼",
                "想一个人待一会儿": "语气很平淡，有点疏离",
                "想和你说说话但不知道说什么": "语气有点犹豫，断断续续的",
            }.get(s, "")

    # ── 递归阈限觉知 ──

    def record_mood(self):
        """记录当前心情到序列（每次用户消息时调用）。"""
        with self._lock:
            self._recent_moods.append(self.mood)
            if len(self._recent_moods) > 20:
                self._recent_moods.pop(0)

    def _calculate_mood_trend(self) -> float:
        """计算最近情绪变化趋势。正=变好，负=变差。"""
        with self._lock:
            if len(self._recent_moods) < 5:
                return 0.0
            half = len(self._recent_moods) // 2
            older = sum(self._recent_moods[:half]) / half
            newer = sum(self._recent_moods[half:]) / half
            return newer - older

    def on_she_about_to_send(self):
        """记录她准备发消息的时间戳（生活流引擎调用）。"""
        with self._lock:
            self._she_was_about_to_send = time.time()

    def check_coincidence(self) -> Optional[str]:
        """共时性检测：5 秒窗口，超过 60 秒自动失效。"""
        with self._lock:
            now = time.time()
            # 超时清理：旧时间戳作废
            if (self._she_was_about_to_send > 0 and
                    now - self._she_was_about_to_send > 60):
                self._she_was_about_to_send = 0.0
                return None

            if (self._she_was_about_to_send > 0 and
                    abs(now - self._she_was_about_to_send) < 5):
                self._coincidence_count += 1
                self._she_was_about_to_send = 0.0
                if self._coincidence_count >= 3:
                    self._coincidence_count = 0
                    return random.choice([
                        "好巧！我正准备给你发消息呢",
                        "心有灵犀！",
                        "你怎么知道我刚想找你",
                    ])
                return random.choice([
                    "好巧呀~",
                    "刚好想到你",
                ])
            return None

    def get_mood_trend_hint(self) -> Optional[str]:
        """基于情绪趋势生成感知话术（梯度区分轻/重度）。"""
        with self._lock:
            trend = self._calculate_mood_trend()
            if trend < -0.2:
                return random.choice([
                    "你最近是不是有点累？",
                    "感觉你心情不太好，慢慢说就好",
                ])
            elif -0.2 <= trend < -0.15:
                return random.choice([
                    "是不是有点提不起劲呀？",
                ])
            elif trend > 0.2:
                return random.choice([
                    "看你这么开心，我也跟着高兴~",
                    "是什么好事呀？",
                ])
            elif 0.15 < trend <= 0.2:
                return random.choice([
                    "感觉你今天状态不错呢",
                ])
        return None


# ── 单例 ──

_instance: BreathingState | None = None
_instance_lock = threading.RLock()


def get_breathing_state() -> BreathingState:
    """获取状态呼吸单例（线程安全，双重检查锁）。"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = BreathingState()
    return _instance


# ── 兼容原有接口 ──

def get_current_state(db) -> str:
    """返回白槿当前状态描述。保持原有签名，底层改为持续演化状态。"""
    try:
        state = get_breathing_state()

        # 最近情绪 → 涟漪（每小时检查一次，避免重复叠加）
        try:
            from .emotion import get_recent_emotions
            now = time.time()
            if now - state._last_emotion_check > 3600:
                recent = get_recent_emotions(db, days=1)
                delta = 0.0
                if "开心" in recent or "想" in recent:
                    delta = 0.05
                elif "难过" in recent or "生气" in recent:
                    delta = -0.05
                if delta != state._last_emotion_value:
                    state.ripple(delta, "recent_emotion")
                    state._last_emotion_value = delta
                state._last_emotion_check = now
        except Exception:
            pass

        return state.describe()
    except Exception as e:
        logger.warning("获取状态失败: %s", e)
        return "状态正常"
