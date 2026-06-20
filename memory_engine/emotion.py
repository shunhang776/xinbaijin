"""
情绪快照。每次对话后保存白槿的第一人称心情，A 级永久记忆。
心情由 Claude Code 通过 --emotion 传入，不传则不保存。规则已封存。
"""
import random
import logging
from .utils import now_ts
from .constants import (
    SECONDS_PER_DAY, EMOTION_SAVE_MIN_INTERVAL, EMOTION_RECENT_DAYS,
    EMOTION_RECENT_LIMIT, EMOTION_OLDER_DAYS, EMOTION_OLDER_LIMIT,
    EMOTION_MAX_TEXT_LEN, EMOTION_ARCHIVE_DAYS,
)

logger = logging.getLogger("memory.emotion")

_last_ts = 0


def save_emotion_snapshot(db, user_msg: str, assistant_reply: str,
                           cloud_snapshot: str | None = None,
                           emotion_event: str | None = None) -> None:
    global _last_ts

    if now_ts() - _last_ts < EMOTION_SAVE_MIN_INTERVAL:
        return

    if cloud_snapshot is None:
        logger.warning("[emotion] cloud_snapshot 为 None，跳过保存——上游不应传空")
        return

    # 清洗：事件文本中不允许出现分隔符，避免检索时拆分错乱
    if emotion_event:
        emotion_event = emotion_event.replace("␞", ",").replace("\n", " ").replace("\t", " ")

    item = {
        "id": f"emo_{now_ts()}_{random.randint(1000, 9999)}",
        "resource_id": "",
        "type": "emotion",
        "content": f"{cloud_snapshot} ␞ {emotion_event}" if emotion_event else cloud_snapshot,
        "tag": "emotion",
        "grade": "A",
        "keywords": "[]",
        "timestamp": now_ts(),
        "created_at": now_ts(),
        "updated_at": now_ts(),
        "access_count": 0,
        "last_access": 0,
        "deleted": 0,
    }
    try:
        db.insert_item(item)
        _last_ts = now_ts()
        logger.info("[emotion] 情绪保存成功: %s", cloud_snapshot[:100])
        if emotion_event:
            logger.info("[emotion] 触发事件: %s", emotion_event[:50])
    except Exception as e:
        logger.warning("[emotion] 情绪快照保存失败: %s", e)


# ==== 以下规则无限期封存，情绪由 Claude Code --emotion 传入 ====
# def _generate_snapshot(user_msg: str, assistant_reply: str) -> str:
#     """根据对话内容生成第一人称情绪快照，每个场景多个随机变体。"""
#     ...（66 行关键词规则已封存，git 历史可恢复）


def get_recent_emotions(db, days: int = EMOTION_RECENT_DAYS) -> str:
    """获取近期情绪快照 + 较早期的情绪样本，形成情绪演变轨迹。"""
    now = now_ts()
    recent_cutoff = now - days * SECONDS_PER_DAY
    older_cutoff = now - EMOTION_OLDER_DAYS * SECONDS_PER_DAY
    recent_rows = _query_emotions(db, recent_cutoff, now, EMOTION_RECENT_LIMIT)
    older_rows = _query_emotions(db, older_cutoff, recent_cutoff, EMOTION_OLDER_LIMIT)

    if not recent_rows and not older_rows:
        return ""

    lines = []
    if recent_rows:
        lines.append("【最近的心情】")
        for r in recent_rows:
            lines.append(_format_line(r, now))
    if older_rows:
        lines.append("【更早的心情】")
        for r in older_rows:
            lines.append(_format_line(r, now))
    return "\n".join(lines)


def _days_ago(ts, now: int) -> str:
    """时间戳转为距今天数的中文描述。"""
    d = (now - int(ts)) // SECONDS_PER_DAY
    if d == 0:
        return "今天"
    if d == 1:
        return "昨天"
    return f"{d}天前"


def _format_line(r, now: int) -> str:
    """格式化单条情绪，剥离时长前缀，分离事件。多 ␞ 时合并后续全部为事件。"""
    tag = _days_ago(r["timestamp"], now)
    parts = r["content"].split(" ␞ ")
    emotion = _strip_duration(parts[0]) or "无特殊心情"
    if len(parts) > 1:
        event = " ␞ ".join(parts[1:]).replace("\n", " ").replace("\t", " ")
        return f"- ({tag}) {emotion}（触发：{event}）"
    return f"- ({tag}) {emotion}"


def _query_emotions(db, ts_from: int, ts_to: int, limit: int) -> list:
    """按时间范围查询未过期情绪。解析 [N天] 前缀，默认 7 天。"""
    now = now_ts()
    rows = db.query(
        "SELECT timestamp, content FROM items WHERE type='emotion' "
        "AND timestamp > ? AND timestamp <= ? AND deleted=0 "
        "ORDER BY timestamp DESC LIMIT 100",
        (ts_from, ts_to),
    )
    valid = []
    for r in rows:
        duration = _parse_duration(r["content"])
        if now < int(str(r["timestamp"])) + duration * SECONDS_PER_DAY:
            valid.append(r)
        if len(valid) >= limit:
            break
    return valid


_CN_NUM = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
           "百": 100, "千": 1000, "万": 10000}


def _cn_to_int(s: str) -> int | None:
    """中文/混合数字 → 整数。如 '十'→10，'三十三'→33，'1百'→100。"""
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    total = 0
    seg = 0
    for c in s:
        if c.isdigit():
            seg = seg * 10 + int(c)
        elif c in _CN_NUM:
            v = _CN_NUM[c]
            if v >= 10:
                seg = (seg or 1) * v
                total += seg
                seg = 0
            else:
                seg = seg * 10 + v
        else:
            return None
    result = total + seg
    return result if result > 0 else None


def _parse_duration(content: str) -> int:
    """扫描全部 [N天] 前缀提取有效期，支持阿拉伯/中文数字，限制 1-36500，默认 7。"""
    remaining = content
    while remaining.startswith("["):
        end = remaining.find("]")
        if end == -1:
            break
        try:
            tag = remaining[1:end].strip()
            if tag.endswith("天"):
                num = tag[:-1]
                d = int(num) if num.isdigit() else _cn_to_int(num)
                if d is not None:
                    d = max(1, min(d, 36500))
                    logger.debug("[emotion] 解析时长: %s → %d天", content[:30], d)
                    return d
        except (ValueError, IndexError):
            pass
        remaining = remaining[end + 1:].lstrip()
    return 7


def cleanup_expired(db, archive_days: int = 365) -> int:
    """物理清理：情绪过期后，再保留 archive_days 天，然后标记删除。"""
    logger.debug("[emotion] 开始执行过期情绪归档清理（archive_days=%d）", archive_days)
    now = now_ts()
    archive_s = archive_days * SECONDS_PER_DAY
    rows = db.query(
        "SELECT id, timestamp, content FROM items WHERE type='emotion' AND deleted=0",
    )
    expired_ids = []
    for r in rows:
        duration = _parse_duration(r["content"])
        expired_at = int(str(r["timestamp"])) + duration * SECONDS_PER_DAY
        if now > expired_at + archive_s:
            expired_ids.append(r["id"])
    if expired_ids:
        placeholders = ",".join(["?"] * len(expired_ids))
        db.conn.execute(
            f"UPDATE items SET deleted=1, updated_at=? WHERE id IN ({placeholders})",
            (now, *expired_ids),
        )
        db.conn.commit()
        logger.info("[emotion] 物理清理 %d 条过期情绪", len(expired_ids))
    return len(expired_ids)


def _strip_duration(content: str) -> str:
    """去掉所有 [N天] 标签（含中文数字），保留其他标签及原始空格。"""
    preserved = []
    remaining = content
    while remaining.startswith("["):
        end = remaining.find("]")
        if end == -1:
            break
        tag = remaining[1:end].strip()
        is_duration = tag.endswith("天") and _cn_to_int(tag[:-1]) is not None
        if not is_duration:
            preserved.append(f"[{tag}]")
        remaining = remaining[end + 1:].lstrip()
    prefix = "".join(preserved)
    if prefix and remaining and not remaining.startswith(" "):
        prefix += " "
    return prefix + remaining
