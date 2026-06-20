"""
平行生活流引擎 — 白槿有自己独立的 24 小时生活。
自指扩展：习惯养成 + 活动记忆 + 引用自己的过去。
高阶语义生成：句式语法树 + 情绪联动 + 口语化变体。

铁律：每函数 ≤ 20 行，线程安全，先处理异常。
"""

import time
import json
import random
import threading
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict
from .config import BASE_DIR

logger = logging.getLogger("memory.life_stream")
_BEIJING = timezone(timedelta(hours=8))
_MAX_PAST_ACTIVITIES = 50
_SELF_REFERENCE_CHANCE = 0.10
_PERSIST_PATH = BASE_DIR / "data" / "life_stream.json"

# 模块级锁（全局语料洗牌用）
_shuffle_lock = threading.RLock()

# ── 句式语法库（按情绪区分，仅结构）──
_SENTENCE_STRUCT = {
    "calm": [
        "伴着{}，慢慢{}", "这会儿{}，正{}",
        "就在{}，随手{}", "趁着{}，安静地{}",
    ],
    "lazy": [
        "懒得动弹，就{}", "慢悠悠地{}，感觉很舒服",
        "放空之余，顺便{}", "窝着不动，只是在{}",
    ],
    "low_mood": [
        "伴着{}，静静{}", "安安静静地{}",
        "没什么事，只是{}", "不想动，就只是{}",
    ],
}

# ── 氛围词组 ──
_ATMOSPHERE = [
    "外面很安静", "阳光正好", "风吹得很轻",
    "有点困意", "心情很放松", "什么都不想管",
    "耳朵里全是旋律", "一个人待着", "周围闹哄哄的",
]

# ── 活动结束感悟（按情绪区分）──
_AFTERGLOW_BY_MOOD = {
    "high": ["心情轻松了不少", "整个人都舒展了", "感觉被治愈了"],
    "normal": ["慢慢静下心来了", "就这样放空也挺好", "心里很平静"],
    "low": ["心里平静了些许", "暂时抛开杂念", "没什么大不了的"],
}

# ── 口语后缀 ──
_SUFFIX = ["呀", "呢", "哦", "~", "啦"]

# ── 活动情绪分类：(名称, 平均分钟, 可打断概率, 时段, 情绪倾向) ──
# 情绪倾向: "low"=低落时更偏好, "high"=高涨时更偏好, "neutral"=中性
BASE_ACTIVITIES = [
    ("翻书",     45, 0.7, (8, 22),  "high"),
    ("听音乐",   30, 0.9, (8, 23),  "low"),
    ("泡茶",     15, 0.8, (9, 21),  "high"),
    ("整理桌面", 60, 0.6, (10, 20), "high"),
    ("望向窗外", 10, 0.7, (7, 23),  "low"),
    ("摆弄绿植", 15, 0.8, (8, 22),  "neutral"),
    ("随手涂鸦", 90, 0.3, (14, 22), "high"),
    ("翻看相册", 20, 0.7, (18, 23), "low"),
    ("闭目休息", 20, 0.5, (12, 22), "low"),
    ("慢悠悠走动", 25, 0.7, (7, 23), "neutral"),
    ("整理杂物", 50, 0.6, (9, 19),  "high"),
    ("喝水发呆", 5,  1.0, (0, 24),  "low"),
    ("翻看随笔", 35, 0.7, (16, 23), "low"),
    ("聆听风声", 12, 0.8, (17, 22), "low"),
    ("打理小盆栽", 12, 0.8, (8, 20), "neutral"),
]


# ── 工具函数 ──
def _in_time(h: int, tr: tuple) -> bool:
    """判断小时是否在时段内（支持跨零点 23→7）。"""
    s, e = tr
    if s <= e:
        return s <= h <= e
    return h >= s or h <= e


class LifeStreamEngine:
    """平行生活流引擎。单例，线程安全。JSON 持久化，重启不丢习惯。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._last_trigger_time: float = time.time()
        self._current_min_interval: float = 0.0
        self._ignore_count: int = 0
        self._current_activity = None
        self._activity_start_time: float = 0.0
        self._was_interrupted: bool = False
        self._search_fn = None  # 依赖注入：联网搜索函数，由 main.py 注入
        self._load_persist()
        self._shuffle_daily()
        logger.info("平行生活流引擎初始化完成")

    def set_search_fn(self, fn):
        """注入联网搜索函数。LifeStream 不直接依赖 internet 模块。"""
        self._search_fn = fn

    def health_check(self) -> tuple[bool, str]:
        """健康自检接口（公开）。返回 (ok: bool, detail: str)。"""
        try:
            self._start_new_activity(0.6)
            name = self._current_activity.get("name", "") if self._current_activity else ""
            return bool(name), name
        except Exception as e:
            return False, str(e)

    # ── 持久化 ──

    def _load_persist(self):
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(_PERSIST_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            self._habits = d.get("habits", {})
            self._past_activities = d.get("past", [])
        except (FileNotFoundError, json.JSONDecodeError):
            self._habits = {}
            self._past_activities = []

    def _save_persist(self):
        with self._lock:
            try:
                with open(_PERSIST_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "habits": self._habits,
                        "past": self._past_activities[-_MAX_PAST_ACTIVITIES:],
                    }, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    # ── 活动管理 ──

    def _start_new_activity(self, mood: float = 0.6):
        with self._lock:
            h = datetime.now(_BEIJING).hour
            available = [a for a in BASE_ACTIVITIES if _in_time(h, a[3])]
            if not available:
                available = BASE_ACTIVITIES

            # 情绪联动权重：低落→安静类，高涨→活跃类
            weights = []
            for a in available:
                tag = a[4]
                if mood < 0.4:       # 情绪低落，偏好安静
                    base_w = 3.0 if tag == "low" else 1.0 if tag == "neutral" else 0.3
                elif mood > 0.7:     # 情绪高涨，偏好活跃
                    base_w = 3.0 if tag == "high" else 1.0 if tag == "neutral" else 0.3
                else:                # 平稳，均匀
                    base_w = 1.0
                # 习惯加成
                habit_w = self._habits.get(a[0], 0) * 0.5 if random.random() < 0.3 else 0
                weights.append(base_w + habit_w)

            total_w = sum(weights)
            r = random.uniform(0, total_w)
            acc = 0.0
            chosen = available[0]
            for i, act in enumerate(available):
                acc += weights[i]
                if r <= acc:
                    chosen = act
                    break

            name, base_min, interruptible, _, _ = chosen
            dur = base_min * random.uniform(0.7, 1.3) * 60

            # 联网丰富：25% 概率尝试获取真实内容注入（必须在构建 activity 前调用）
            enrich = self._try_enrich_activity(name)

            self._current_activity = {
                "name": name, "duration": dur,
                "interruptible": interruptible,
                "enrich": enrich,
                "interrupt_high": ["正入神呢", "刚静下心来", "差点沉浸进去了"],
                "interrupt_low": ["刚好歇一歇", "正无聊呢", "来得正好呀"],
            }
            self._activity_start_time = time.time()
            self._was_interrupted = False

            self._past_activities.append(
                {"name": name, "start_time": time.time(), "enrich": enrich})
            if len(self._past_activities) > _MAX_PAST_ACTIVITIES:
                self._past_activities.pop(0)
            self._habits[name] = self._habits.get(name, 0) + 1
            self._save_persist()
            logger.info("[LifeStream] 开始活动: %s, 时长: %.0fs, 可打断: %.0f%%, 习惯: %d, 情绪联动: %.2f→%s%s",
                        name, dur, interruptible * 100, self._habits[name],
                        mood, "活跃偏好" if mood > 0.7 else "安静偏好" if mood < 0.4 else "中性",
                        f", 联网内容: {enrich[:30]}" if enrich else "")

    # ── 联网活动丰富 ──

    _ENRICH_QUERIES = {
        "翻书": "今日趣味冷知识",
        "翻看随笔": "生活中的小发现或感悟",
        "翻看相册": "最近有趣的旅行或生活照片故事",
        "听音乐": "最近好听的华语新歌推荐",
        "泡茶": "茶文化趣味小知识",
        "随手涂鸦": "简单好看的简笔画教程",
        "打理小盆栽": "新手盆栽养护小技巧",
    }
    _ENRICH_CHANCE = 0.25

    def _try_enrich_activity(self, name: str) -> str:
        """用联网搜索丰富活动内容。搜索函数由 main.py 注入，无注入时静默跳过。"""
        if not self._search_fn:
            return ""
        query = self._ENRICH_QUERIES.get(name)
        if not query or random.random() > self._ENRICH_CHANCE:
            return ""
        try:
            results = self._search_fn(query, limit=1)
            if results:
                snippet = results[0].get("snippet", "")[:80]
                if snippet:
                    logger.info("[LifeStream] 联网丰富活动: %s -> %s", name, snippet[:40])
                    return snippet
        except Exception as e:
            logger.debug("[LifeStream] 联网丰富失败: %s", e)
        return ""

    # ── 自引用 ──

    def get_past_reference(self) -> Optional[str]:
        with self._lock:
            if (not self._past_activities or
                    random.random() > _SELF_REFERENCE_CHANCE):
                return None
            past = random.choice(self._past_activities[-10:])
            days = (time.time() - past["start_time"]) / 86400
            prefix = ("昨天" if days < 1
                      else "前几天" if days < 7
                      else "上次")
            return f"{prefix}{past['name']}的时候"

    # ── 消息构建 ──

    def _build_message(self, event: dict, mood: float = 0.6) -> str:
        with self._lock:
            name = event.get("name", "发呆")
            enrich = event.get("enrich", "")
            atmos = random.choice(_ATMOSPHERE)
            if mood > 0.7:
                style, sp = "calm", 0.4
            elif mood < 0.4:
                style, sp = "low_mood", 0.1
            else:
                style, sp = "lazy", 0.3
            # 有联网内容时，用具体描述替代活动名称
            display = enrich if enrich else name
            msg = random.choice(_SENTENCE_STRUCT[style]).format(atmos, display)
            if random.random() < sp:
                msg += random.choice(_SUFFIX)
            return msg

    def _get_activity_status(self, mood: float = 0.6) -> str:
        with self._lock:
            cur = self._current_activity
            if not cur:
                return "暂时放空，没做什么"
            name = cur.get("enrich", "") or cur["name"]
            atmos = random.choice(_ATMOSPHERE)
            if mood > 0.7:
                style = "calm"
            elif mood < 0.4:
                style = "low_mood"
            else:
                style = "lazy"
            return random.choice(_SENTENCE_STRUCT[style]).format(atmos, name)

    def _get_afterglow(self, mood: float = 0.6) -> str:
        with self._lock:
            if mood > 0.7:
                return random.choice(_AFTERGLOW_BY_MOOD["high"])
            elif mood < 0.4:
                return random.choice(_AFTERGLOW_BY_MOOD["low"])
            return random.choice(_AFTERGLOW_BY_MOOD["normal"])

    def _shuffle_daily(self):
        with _shuffle_lock:
            random.shuffle(_ATMOSPHERE)
            for k in _SENTENCE_STRUCT:
                random.shuffle(_SENTENCE_STRUCT[k])
            logger.info("[LifeStream] 每日语料洗牌完成, 氛围词: %d, 句式组: %d",
                        len(_ATMOSPHERE), len(_SENTENCE_STRUCT))

    # ── 用户交互 ──

    def on_user_message(self, mood: float = 0.6) -> str:
        with self._lock:
            self._ignore_count = 0
            now = time.time()
            if (
                not self._current_activity or
                now - self._activity_start_time >
                self._current_activity["duration"]
            ):
                self._start_new_activity(mood)

            interruptible = self._current_activity["interruptible"]
            if random.random() < interruptible:
                self._was_interrupted = True
                if interruptible < 0.4:
                    resp = random.choice(
                        self._current_activity["interrupt_high"])
                else:
                    resp = random.choice(
                        self._current_activity["interrupt_low"])
                logger.info("[LifeStream] 用户打断活动: %s, 响应: %s",
                            self._current_activity["name"], resp)
                return resp
            resp = random.choice(["嗯？", "在呢", "怎么啦"])
            logger.debug("[LifeStream] 用户消息，未打断: %s",
                         self._current_activity["name"])
            return resp

    def on_ignored(self):
        with self._lock:
            self._ignore_count += 1
            self._save_persist()

    # ── 主 tick ──

    def tick(self, current_mood: float = 0.6) -> Optional[str]:
        with self._lock:
            now = time.time()

            # 活动结束 → 感悟
            if (
                self._current_activity and
                now - self._activity_start_time >
                self._current_activity["duration"]
            ):
                act_name = self._current_activity["name"]
                if random.random() < 0.3 and not self._was_interrupted:
                    try:
                        from memory_engine.state import get_breathing_state
                        get_breathing_state().on_she_about_to_send()
                    except Exception:
                        pass
                    r = self._get_afterglow(current_mood)
                    logger.info("[LifeStream] 活动结束感悟: %s → %s", act_name, r)
                    self._current_activity = None
                    return r
                logger.debug("[LifeStream] 活动结束无感悟: %s", act_name)
                self._current_activity = None

            # 最小间隔
            if (self._current_min_interval > 0 and
                    now - self._last_trigger_time <
                    self._current_min_interval):
                return None

            h = datetime.now(_BEIJING).hour
            if 23 <= h or h < 7:
                return None

            # 被无视系数
            ignore_coeff = max(0.2, 1.0 - self._ignore_count * 0.12)
            if random.random() > 0.02 * ignore_coeff:
                return None

            if not self._current_activity:
                self._start_new_activity(current_mood)
            # 记录准备发消息时间戳（共时检测用）
            try:
                from memory_engine.state import get_breathing_state
                get_breathing_state().on_she_about_to_send()
            except Exception:
                pass

            event = self._current_activity
            self._last_trigger_time = now
            self._current_min_interval = random.randint(3, 14) * 86400
            msg = self._build_message(event, current_mood)
            logger.info("[LifeStream] 主动分享: %s (情绪: %.2f, 忽略计数: %d)",
                        msg, current_mood, self._ignore_count)
            return msg


# ── 单例 ──
_instance: Optional[LifeStreamEngine] = None
_instance_lock = threading.RLock()


def get_life_stream_engine(db=None) -> LifeStreamEngine:
    """兼容旧调用，db 参数保留但不使用。"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = LifeStreamEngine()
    return _instance
