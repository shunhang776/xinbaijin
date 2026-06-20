"""
零规则纯上下文 prompt 构建器。
只给信息，不给规则，白槿自己决定怎么说。
心情不由外部注入——白槿回复时自然感知，回复后通过 --emotion 落盘。
"""
import math
import time
import logging
import threading
from collections import deque

logger = logging.getLogger(__name__)
from memory_engine.retrieve import get_relevant_memories, format_memory_for_prompt
from memory_engine.emotion import get_recent_emotions
from memory_engine.preferences import get_preferences
from memory_engine.state import get_current_state
from memory_engine.recent_chat import format_recent_chat
from memory_engine.retrieve_core import _classify_emotion_type
from memory_engine.config import SENSE_ENABLE, WEATHER_SHOW_NUM

_WEATHER_KEYWORDS = [
    "天气", "几度", "冷吗", "热吗", "冷不冷", "热不热",
    "下雨", "下雪", "刮风", "温度", "凉吗", "凉不凉",
    "降几", "多少度", "气温", "带伞", "带雨", "湿度",
]

MOOD_HINTS = {
    "happy": "你现在的心情偏向开心", "sad": "你现在的心情有点低沉",
    "angry": "你现在的心情有点烦躁", "anxious": "你现在有点担心",
    "proud": "你现在挺有成就感的", "tender": "你现在心里很柔软",
    "confused": "你现在有点迷茫", "longing": "你有点想念顺航",
    "neutral": "你现在心情比较平静",
}

TRIGGER_HINTS = {
    "happy": "聊到这些让你又开心起来", "sad": "想起来还是有点难过",
    "angry": "想到这些还是有点不爽", "anxious": "想起来还是有点担心",
    "proud": "让你又想起了那时候的成就感", "tender": "想起来心里还是软软的",
    "longing": "让你有点想他",
}

EMOTION_HALFLIFE_DAYS = 30  # 情绪衰减半衰期
MEMORY_COOLDOWN_SEC = 3600   # 同一记忆情绪触发冷却
TRIGGER_CACHE_TTL = 300      # 无记忆时触发缓存的 TTL


def _is_weather_query(msg: str) -> bool:
    return any(kw in msg for kw in _WEATHER_KEYWORDS)


try:
    from memory_engine.senses import get_sense_collector
    _HAS_SENSES = True
except ImportError:
    _HAS_SENSES = False


# ── 情绪触发共享状态 ──
_trigger_lock = threading.RLock()
_triggered_cache: deque = deque(maxlen=3)
_last_trigger_ts: float = 0.0
_memory_cooldown: dict[str, float] = {}


def _get_current_emotion_type(db, now: int | None = None) -> str:
    """从数据库读取最近情绪并归类，返回情绪类型标签。"""
    if now is None:
        now = int(time.time())
    rows = db.query(
        "SELECT content FROM items WHERE type='emotion' AND deleted=0 "
        "AND timestamp > ? ORDER BY timestamp DESC LIMIT 3",
        (now - 86400,))
    if not rows:
        return "neutral"
    content = rows[0]["content"]
    for tag in ("[强]", "[中]", "[微]"):
        if content.startswith(tag):
            content = content[3:].strip()
    return _classify_emotion_type(content)


def _compute_triggered_hints(memories, current_etype: str, now: int | None = None) -> list[str]:
    """扫描关联记忆中的情绪，返回被触发的情绪提示列表。"""
    if now is None:
        now = int(time.time())
    triggered: dict[str, float] = {}
    for mem in memories:
        mem_id = mem.get("id", "")
        if mem_id in _memory_cooldown and now - _memory_cooldown[mem_id] < MEMORY_COOLDOWN_SEC:
            continue
        mem_ts = mem.get("timestamp", now)
        for e in mem.get("associated_emotions", []):
            if not isinstance(e, dict):
                continue
            etype = e.get("type", "")
            if not etype or etype in ("neutral", current_etype):
                continue
            days = (now - mem_ts) / 86400
            intensity = e.get("intensity", 3)
            adj = intensity * math.exp(-days * math.log(2) / EMOTION_HALFLIFE_DAYS)
            if adj > triggered.get(etype, 0):
                triggered[etype] = adj

    if not triggered:
        return []

    sorted_emotions = sorted(triggered.items(), key=lambda x: -x[1])
    hints = []
    for etype, adj in sorted_emotions[:2]:
        if adj >= 3 and etype in TRIGGER_HINTS:
            hints.append(TRIGGER_HINTS[etype])
            with _trigger_lock:
                for mem in memories:
                    for e in mem.get("associated_emotions", []):
                        if isinstance(e, dict) and e.get("type") == etype:
                            _memory_cooldown[mem.get("id", "")] = now

    if hints:
        with _trigger_lock:
            for etype, _ in sorted_emotions[:1]:
                _triggered_cache.append(etype)
            global _last_trigger_ts
            _last_trigger_ts = now
    return hints


def _apply_cached_trigger(current_etype: str, now: int | None = None) -> str:
    """无关联记忆时，检查最近的触发缓存是否仍有效。"""
    if now is None:
        now = int(time.time())
    if now - _last_trigger_ts >= TRIGGER_CACHE_TTL or not _triggered_cache:
        return ""
    with _trigger_lock:
        for etype in reversed(_triggered_cache):
            if etype != current_etype and etype in TRIGGER_HINTS:
                return f"。{TRIGGER_HINTS[etype]}"
    return ""


def _get_mood_hint(db, memories: list[dict] | None = None) -> str:
    now = int(time.time())
    current_etype = _get_current_emotion_type(db, now)
    hint = MOOD_HINTS.get(current_etype, "你现在心情比较平静")

    if memories:
        triggered_hints = _compute_triggered_hints(memories, current_etype, now)
        if triggered_hints:
            hint += "。" + "，也".join(triggered_hints)
    else:
        hint += _apply_cached_trigger(current_etype, now)

    return f"【你当下的心情】\n{hint}"


# ── prompt 各模块独立构建函数（每个 ≤ 20 行）──

def _build_cloud_section(engine, user_msg: str, recent_chat: str,
                         total_memories: int, query_vec):
    """获取语义云和候选记忆。"""
    if not (engine and hasattr(engine, "backgrounder")):
        return "", None, None
    try:
        ctx = recent_chat[-200:] if recent_chat else ""
        return engine.backgrounder.get_cloud_and_candidates(
            user_msg, recent_context=ctx,
            total_memories=total_memories, query_vec=query_vec)
    except Exception:
        logger.debug("builder cloud 失败", exc_info=True)
        return "", None, None


def _build_sense_section(recent_chat: str, user_msg: str) -> str:
    """构建感官快照文本。"""
    if not (SENSE_ENABLE and _HAS_SENSES):
        return ""
    try:
        collector = get_sense_collector()
        msg_count = sum(1 for line in recent_chat.split("\n")
                        if line.strip().startswith("顺航：")) if recent_chat else 0
        snap = collector.snapshot(msg_count=msg_count)
        text = collector.format_prompt(snap)
        if WEATHER_SHOW_NUM or _is_weather_query(user_msg):
            wd = snap.get("weather_detail", "")
            if wd:
                text += f"\n天气：{wd}"
        return text
    except Exception:
        logger.debug("builder sense 失败", exc_info=True)
        return ""


def _build_life_section() -> str:
    """构建平行生活流当前活动文本。"""
    try:
        from memory_engine.life_stream import get_life_stream_engine
        from memory_engine.state import get_breathing_state
        eng = get_life_stream_engine()
        bmood = get_breathing_state().mood
        note = eng._get_activity_status(bmood)
        ref = eng.get_past_reference()
        return f"{note}（{ref}）" if ref else note
    except Exception:
        logger.debug("builder life 失败", exc_info=True)
        return ""


def _build_idle_section() -> str:
    """构建内在独白文本。"""
    try:
        from memory_engine.idle_thoughts import get_idle_thought_store
        active = get_idle_thought_store().get_active()
        if not active:
            return ""
        lines = ["【此刻掠过的念头】"]
        for t in active:
            lines.append(f"- {t.content}")
        return "\n".join(lines)
    except Exception:
        logger.debug("builder idle 失败", exc_info=True)
        return ""


def _build_liminal_section() -> str:
    """构建阈限状态文本（情绪趋势 + 语气修饰）。"""
    try:
        from memory_engine.state import get_breathing_state
        st = get_breathing_state()
        parts: list[str] = []
        trend = st.get_mood_trend_hint()
        if trend:
            parts.append(trend)
        tone = st.get_reply_tone_modifier()
        if tone:
            parts.append(f"【此刻的语气】{tone}")
        return "\n".join(parts) if parts else ""
    except Exception:
        logger.debug("builder liminal 失败", exc_info=True)
        return ""


def _build_intuition_section(user_msg: str, recent_chat: str, bg_query_vec) -> str:
    """构建社交直觉文本。"""
    try:
        from memory_engine.common_sense import get_social_intuition
        text = get_social_intuition(user_msg, recent_chat, query_vec=bg_query_vec)
        return f"你心里留意到：\n{text}" if text else ""
    except Exception:
        logger.debug("builder intuition 失败", exc_info=True)
        return ""


def _get_reminder_text(db) -> str:
    """获取到期提醒文本。"""
    try:
        from memory_engine.reminder import get_due_reminders, mark_reminder_done
        reminders = get_due_reminders(db)
        if not reminders:
            return ""
        lines = ["【需要记得的事】"]
        for rid, content in reminders:
            lines.append(f"- {content}")
            mark_reminder_done(db, rid)
        return "\n".join(lines)
    except ImportError:
        pass
    return ""


def _load_identity() -> str:
    """加载白槿身份文本。"""
    try:
        with open("data/persona.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except (FileNotFoundError, OSError):
        return "你是白槿，和顺航在一起很久了，嘴硬心软，很在乎他。"


# ── 主编排器 ──

def build_prompt(db, user_msg: str, sender_type: str = "qq", query_vec=None) -> tuple[str, str]:
    """组装完整的系统 prompt。每个模块独立构建，一个失败不影响其他。"""
    from memory_engine import get_engine
    engine = get_engine()
    total_memories = len(engine.time_index) if engine else 0

    recent_chat = format_recent_chat(db, n=6)
    cloud, candidates, bg_query_vec = _build_cloud_section(
        engine, user_msg, recent_chat, total_memories, query_vec)

    memories = get_relevant_memories(user_msg, prefetched=candidates, query_vec=query_vec)
    emotions = get_recent_emotions(db)
    state = get_current_state(db)
    life_note = _build_life_section()

    parts = [
        f"【你是谁】\n{_load_identity()}",
        f"【你现在的状态】\n{state}",
        _build_sense_section(recent_chat, user_msg),
        f"【你正在做什么】\n{life_note}" if life_note else "",
        _build_liminal_section(),
        _build_idle_section(),
        _build_intuition_section(user_msg, recent_chat, bg_query_vec),
        recent_chat if recent_chat else "",
        f"【顺航刚说】\n{user_msg}",
        format_memory_for_prompt(memories, sender_type),
        _get_mood_hint(db, memories),
        emotions,
        get_preferences(),
        _get_reminder_text(db),
        "请用白槿的身份，自然地回复顺航。怎么说都行，不用刻意，不用遵守任何规则。",
    ]
    system_prompt = "\n\n".join(p for p in parts if p)
    return system_prompt, cloud
