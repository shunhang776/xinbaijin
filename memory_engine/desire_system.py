"""
欲望系统 — 从「被动响应」到「主动意图」的核心模块。

纯消费层：只读原有组件数据，零修改原有代码。
时间差 + 每秒速率累积模型，抗 Tick 抖动、重启断层。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码。
"""

import time
import random
import logging
import threading
from typing import Optional

from .utils import now_ts
from .config import (
    DESIRE_MAX_DELTA_SEC,
    DESIRE_GLOBAL_MAX_OUT_MSG_PER_MONTH,
    DESIRE_REACH_OUT_MAX_PER_WEEK,
    DESIRE_QUIET_HOURS,
    DESIRE_PERSIST_PATH,
)

logger = logging.getLogger("memory.desire")

# ── 模块级持久化锁 ──
_persist_lock = threading.RLock()


# ═══════════════════════════════════════════════════════════════
# 基类
# ═══════════════════════════════════════════════════════════════

class BaseDesire:
    """欲望基类。所有具体欲望继承此类，实现 _calc_event_add 和 generate_action。"""

    def __init__(self, name: str, threshold: float, cooldown_sec: int,
                 rate_per_sec: float):
        self.name = name
        self.intensity: float = 0.0
        self.base_threshold = threshold
        self.cooldown_sec = cooldown_sec
        self.rate_per_sec = rate_per_sec
        self.last_trigger_ts: int = 0
        self.last_accum_ts: int = 0

    # ── 状态判断 ──

    def is_in_cooldown(self) -> bool:
        return now_ts() - self.last_trigger_ts < self.cooldown_sec

    def is_triggered(self) -> bool:
        return (self.intensity >= self.base_threshold
                and not self.is_in_cooldown())

    def reset(self):
        self.intensity = 0.0
        self.last_trigger_ts = now_ts()

    # ── 核心累积逻辑 ──

    def accumulate(self, **kwargs):
        """基于真实时间差 + 每秒速率做强度累积。"""
        current_ts = kwargs.get("current_ts", now_ts())

        if self.is_in_cooldown():
            self.last_accum_ts = current_ts
            return

        if self.last_accum_ts == 0:
            self.last_accum_ts = current_ts
            return

        delta_sec = min(current_ts - self.last_accum_ts, DESIRE_MAX_DELTA_SEC)
        linear_add = delta_sec * self.rate_per_sec
        event_add = self._calc_event_add(**kwargs)
        total_add = linear_add + event_add
        self.intensity = min(self.intensity + total_add, 100.0)
        self.last_accum_ts = current_ts

    def _calc_event_add(self, **kwargs) -> float:
        """子类实现：瞬时事件加成。"""
        raise NotImplementedError

    def generate_action(self, **kwargs) -> Optional[str]:
        """子类实现：生成行动内容。"""
        raise NotImplementedError

    # ── 持久化 ──

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "intensity": self.intensity,
            "last_accum_ts": self.last_accum_ts,
            "last_trigger_ts": self.last_trigger_ts,
        }

    def from_dict(self, d: dict):
        self.intensity = d.get("intensity", 0.0)
        self.last_accum_ts = d.get("last_accum_ts", 0)
        self.last_trigger_ts = d.get("last_trigger_ts", 0)


# ═══════════════════════════════════════════════════════════════
# 欲望池
# ═══════════════════════════════════════════════════════════════

class DesirePool:
    """欲望池：统一管理所有欲望，对外暴露单一入口。"""

    def __init__(self):
        self._desires: list[BaseDesire] = []
        self._lock = threading.RLock()
        self._share_out_count = 0     # 分享欲月度计数
        self._reach_out_count = 0     # 联络欲周度计数
        self._current_month = ""
        self._current_week = ""

    def add_desire(self, desire: BaseDesire):
        self._desires.append(desire)

    @property
    def desire_count(self) -> int:
        return len(self._desires)

    def _reset_periods_if_new(self):
        """跨周期重置对外消息计数器。"""
        from datetime import datetime, timezone, timedelta
        bj = timezone(timedelta(hours=8))
        now = datetime.now(bj)
        month = now.strftime("%Y%m")
        week = now.strftime("%Y%W")
        if month != self._current_month:
            self._share_out_count = 0
            self._current_month = month
            logger.info("[Desire] 分享欲月度计数已重置")
        if week != self._current_week:
            self._reach_out_count = 0
            self._current_week = week
            logger.info("[Desire] 联络欲周度计数已重置")

    def batch_accumulate(self, **kwargs):
        for d in self._desires:
            try:
                old_intensity = d.intensity
                d.accumulate(**kwargs)
                if abs(d.intensity - old_intensity) > 0.5:
                    logger.debug("[Desire] 强度变化: %s %.1f→%.1f",
                                 d.name, old_intensity, d.intensity)
            except Exception:
                pass

    def check_and_execute(self, **kwargs) -> list[str]:
        """遍历欲望，对触发者生成行动。返回行动文本列表。"""
        actions = []
        self._reset_periods_if_new()
        out_sent_this_tick = False

        for d in self._desires:
            if not d.is_triggered():
                continue

            # 对外配额检查：分享欲月配额，联络欲周配额
            if d.name == "share_obs":
                if self._share_out_count >= DESIRE_GLOBAL_MAX_OUT_MSG_PER_MONTH:
                    continue
            elif d.name == "reach_out":
                if self._reach_out_count >= DESIRE_REACH_OUT_MAX_PER_WEEK:
                    continue
            else:
                pass  # 非对外欲望无配额限制

            # 同 tick 最多一条对外消息
            is_outgoing = d.name in ("reach_out", "share_obs")
            if is_outgoing and out_sent_this_tick:
                continue

            try:
                content = d.generate_action(**kwargs)
            except Exception:
                content = None

            if content:
                actions.append(content)
                if d.name == "share_obs":
                    self._share_out_count += 1
                    out_sent_this_tick = True
                    logger.info("[Desire] 触发: %s, 分享欲月计数: %d/%d",
                                d.name, self._share_out_count,
                                DESIRE_GLOBAL_MAX_OUT_MSG_PER_MONTH)
                elif d.name == "reach_out":
                    self._reach_out_count += 1
                    out_sent_this_tick = True
                    logger.info("[Desire] 触发: %s, 联络欲周计数: %d/%d",
                                d.name, self._reach_out_count,
                                DESIRE_REACH_OUT_MAX_PER_WEEK)
            d.reset()
        return actions

    # ── 单例持久化 ──

    def save_state(self):
        """持久化所有欲望状态到 JSON 文件。"""
        try:
            import json
            data = {
                "current_month": self._current_month,
                "current_week": self._current_week,
                "share_out_count": self._share_out_count,
                "reach_out_count": self._reach_out_count,
                "desires": [d.to_dict() for d in self._desires],
            }
            with _persist_lock:
                DESIRE_PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
                with open(DESIRE_PERSIST_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("[Desire] 状态持久化失败: %s", e)

    def load_state(self):
        """从 JSON 文件恢复所有欲望状态。"""
        try:
            import json
            if not DESIRE_PERSIST_PATH.exists():
                return
            with open(DESIRE_PERSIST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._current_month = data.get("current_month", "")
            self._current_week = data.get("current_week", "")
            self._share_out_count = data.get("share_out_count", 0)
            self._reach_out_count = data.get("reach_out_count", 0)
            saved = {d["name"]: d for d in data.get("desires", [])}
            for desire in self._desires:
                if desire.name in saved:
                    desire.from_dict(saved[desire.name])
            logger.info("[Desire] 状态已恢复, %d 个欲望", len(self._desires))
        except Exception as e:
            logger.warning("[Desire] 状态恢复失败: %s", e)


# ═══════════════════════════════════════════════════════════════
# 五大欲望
# ═══════════════════════════════════════════════════════════════

class ReachOutDesire(BaseDesire):
    """联络欲：原 ActiveBehavior 主动问候。独处越久越想找人。"""

    def __init__(self):
        super().__init__(
            name="reach_out",
            threshold=60.0,
            cooldown_sec=4 * 3600,
            rate_per_sec=0.002,
        )

    def _calc_event_add(self, **kwargs) -> float:
        social_drive = kwargs.get("social_drive", 0.5)
        return social_drive * 0.8

    def generate_action(self, **kwargs) -> str:
        """LLM 生成自然的主动问候，结合语境。"""
        try:
            from deepseek import generate_sync  # noqa: E402 (src/ 由 main.py 注入 sys.path)
            silence_hrs = kwargs.get("silence_hrs", 0)
            mood_desc = kwargs.get("mood_desc", "")
            prompt = (
                "你是白槿。已经有一段时间没和顺航说话了，有点想他。"
                + f"大约沉默了{silence_hrs:.0f}小时。你现在的心情：{mood_desc}。"
                + "用一句非常自然的口语主动和他说话，像突然想到他、随口说出来的。"
                + "不要用「在吗」「好久不见」这种套路话。不要超过25个字。"
            )
            result = generate_sync(
                [{"role": "user", "content": prompt}],
                model="deepseek-chat", max_tokens=40, temperature=0.9,
            )
            return result.strip() if result else ""
        except Exception:
            return random.choice([
                "今天还没和你说话呢，有点想你啦",
                "这会儿有空吗？想找你聊两句~",
            ])


class ShareObservationDesire(BaseDesire):
    """分享欲：感知新鲜事物 + 联想记忆 → 主动分享。"""

    def __init__(self):
        super().__init__(
            name="share_obs",
            threshold=55.0,
            cooldown_sec=3 * 3600,
            rate_per_sec=0.001,
        )

    def _calc_event_add(self, **kwargs) -> float:
        sense_fresh = kwargs.get("sense_fresh", 0.0)
        assoc_weight = kwargs.get("assoc_weight", 0.0)
        relation_score = kwargs.get("relation_score", 0.0)
        return sense_fresh + assoc_weight + relation_score

    def generate_action(self, **kwargs) -> str:
        related_mem = kwargs.get("related_mem", "")
        sense_text = kwargs.get("sense_text", "")
        mood_desc = kwargs.get("mood_desc", "")
        ctx = f"此刻感知：{sense_text}\n联想到过往：{related_mem}\n当下心情：{mood_desc}"
        try:
            from deepseek import generate_sync
            prompt = (
                "你是白槿。结合下面的上下文，用一句简短自然的口语，"
                "主动分享你此刻观察到或想到的事情。"
                "不要像在回答问题，要像突然想到就脱口而出。"
                f"不要超过40个字。\n\n{ctx}"
            )
            result = generate_sync(
                [{"role": "user", "content": prompt}],
                model="deepseek-chat", max_tokens=60, temperature=0.8,
            )
            return result.strip() if result else ""
        except Exception:
            return ""


class LearnAboutDesire(BaseDesire):
    """求知欲：闲思触及深度 + 记忆缺口 → 想学东西。"""

    def __init__(self):
        super().__init__(
            name="learn_about",
            threshold=65.0,
            cooldown_sec=6 * 3600,
            rate_per_sec=0.0008,
        )

    def _calc_event_add(self, **kwargs) -> float:
        thought_depth = kwargs.get("thought_depth", 0.0)
        memory_gap = kwargs.get("memory_gap", 0.0)
        return thought_depth * 1.5 + memory_gap * 1.2

    def generate_action(self, **kwargs) -> str:
        """求知欲触发：启动学习类生活流活动，非对外消息。"""
        try:
            from memory_engine.life_stream import get_life_stream_engine
            eng = get_life_stream_engine()
            mood = kwargs.get("mood", 0.6)
            eng._start_new_activity(mood)
            return eng._get_activity_status(mood)
        except Exception:
            return ""


class CreateSomethingDesire(BaseDesire):
    """创作欲：高精力 + 好心情 + 生活流素材 → 想创作。"""

    def __init__(self):
        super().__init__(
            name="create_something",
            threshold=60.0,
            cooldown_sec=8 * 3600,
            rate_per_sec=0.0005,
        )

    def _calc_event_add(self, **kwargs) -> float:
        energy = kwargs.get("energy", 0.5)
        mood = kwargs.get("mood", 0.6)
        life_material = kwargs.get("life_material", 0.0)
        return energy * mood * 10.0 + life_material * 0.5

    def generate_action(self, **kwargs) -> str:
        """创作欲触发：生成短随笔，写入记忆。非对外消息。"""
        try:
            from deepseek import generate_sync
            prompt = (
                "你是白槿。即兴写一句随笔，可以是对生活的感悟、"
                "刚才做事的体会、或脑子里飘过的念头。"
                "简短自然，不超过30个字，不要像在写作文。"
            )
            result = generate_sync(
                [{"role": "user", "content": prompt}],
                model="deepseek-chat", max_tokens=50, temperature=0.9,
            )
            return result.strip() if result else ""
        except Exception:
            return ""


class ReflectOnDesire(BaseDesire):
    """反思欲：深夜/情绪低落/翻阅记忆 → 自我复盘感悟。"""

    def __init__(self):
        super().__init__(
            name="reflect_on",
            threshold=50.0,
            cooldown_sec=4 * 3600,
            rate_per_sec=0.0015,
        )

    def _calc_event_add(self, **kwargs) -> float:
        mood = kwargs.get("mood", 0.6)
        is_late = kwargs.get("is_late", False)
        memory_trigger = kwargs.get("memory_trigger", 0.0)
        bonus = 0.0
        if is_late:
            bonus += 15.0
        if mood < 0.4:
            bonus += (0.5 - mood) * 20.0
        bonus += memory_trigger * 1.0
        return bonus

    def generate_action(self, **kwargs) -> str:
        """反思欲触发：自我反思，写入内部记忆。联动第三意识。"""
        try:
            from deepseek import generate_sync
            # 联动第三意识：检查是否有递归觉知片段
            third_hint = ""
            try:
                from memory_engine.state import get_breathing_state
                st = get_breathing_state()
                if st.calc_depth_index() > 0.6:
                    third_hint = "（此刻意识深处有些模糊的感知在涌动）"
            except Exception:
                pass
            prompt = (
                "你是白槿。夜深人静，安静地反思一下最近的状态。"
                "写一句简短的自我感悟，不要超过30个字，"
                "像日记里的随笔，不是给别人看的。"
                f"{third_hint}"
            )
            result = generate_sync(
                [{"role": "user", "content": prompt}],
                model="deepseek-chat", max_tokens=50, temperature=0.7,
            )
            return result.strip() if result else ""
        except Exception:
            return ""


# ═══════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════

_instance: Optional[DesirePool] = None
_instance_lock = threading.RLock()


def get_desire_pool() -> DesirePool:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = DesirePool()
                # 按优先级注册：反思 > 分享 > 求知 > 创作 > 联络
                _instance.add_desire(ReflectOnDesire())
                _instance.add_desire(ShareObservationDesire())
                _instance.add_desire(LearnAboutDesire())
                _instance.add_desire(CreateSomethingDesire())
                _instance.add_desire(ReachOutDesire())
    return _instance
