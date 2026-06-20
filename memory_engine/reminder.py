"""
待关心事件提醒。由 Claude Code 通过 --reminder 参数传入，
到期后触发主动消息。类型为 reminder，A 级永久记忆。
"""
import logging
from .utils import now_ts
from .constants import REMINDER_AHEAD_SECONDS, REMINDER_DEDUP_WINDOW

logger = logging.getLogger("memory.reminder")


def save_reminder(db, content: str, trigger_time: int) -> None:
    """保存一条待关心事件提醒。24 小时内相同内容去重。"""
    existing = db.query(
        "SELECT id FROM items WHERE type='reminder' AND content=? "
        "AND timestamp > ? AND deleted=0",
        (content, now_ts() - REMINDER_DEDUP_WINDOW),
    )
    if existing:
        logger.info("[reminder] 重复提醒，跳过: %s", content[:50])
        return
    import random
    rid = f"rem_{now_ts()}_{random.randint(1000, 9999)}"
    item = {
        "id": rid, "resource_id": "", "type": "reminder",
        "content": content, "tag": "reminder", "grade": "A",
        "keywords": "[]", "timestamp": trigger_time,
        "created_at": now_ts(), "updated_at": now_ts(),
        "access_count": 0, "last_access": 0, "deleted": 0,
    }
    try:
        db.insert_item(item)
        logger.info("[reminder] 已保存: %s，触发: %d", content[:50], trigger_time)
    except Exception as e:
        logger.warning("[reminder] 保存失败: %s", e)


def get_due_reminders(db) -> list[str]:
    """获取当前到期的未完成提醒。"""
    now = now_ts()
    cutoff = now + REMINDER_AHEAD_SECONDS
    rows = db.query(
        "SELECT id, content FROM items WHERE type='reminder' "
        "AND timestamp > ? AND timestamp <= ? AND deleted=0 "
        "ORDER BY timestamp ASC",
        (now, cutoff),
    )
    return [(r["id"], r["content"]) for r in rows]


def mark_reminder_done(db, rid: str) -> None:
    """标记提醒为已完成，避免重复触发。"""
    try:
        db.execute(
            "UPDATE items SET deleted=1, updated_at=? WHERE id=? AND type='reminder'",
            (now_ts(), rid),
        )
        logger.info("[reminder] 标记完成: %s", rid)
    except Exception as e:
        logger.warning("[reminder] 标记完成失败: %s", e)


def add(trigger_at: str, content: str) -> str:
    """兼容旧接口：ISO 时间字符串 → epoch → 写入 DB。由 tools.py set_reminder 工具调用。"""
    from datetime import datetime, timezone, timedelta
    try:
        dt = datetime.fromisoformat(trigger_at)
        trigger_ts = int(dt.timestamp())
    except (ValueError, TypeError):
        return f"时间格式错误: {trigger_at}"
    from . import get_engine
    engine = get_engine()
    if not engine:
        return "引擎未就绪"
    save_reminder(engine.db, content, trigger_ts)
    return f"已设置提醒: {content}"
