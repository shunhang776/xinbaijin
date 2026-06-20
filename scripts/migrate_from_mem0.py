"""
从 Mem0 迁移旧记忆到新引擎。
用法: python scripts/migrate_from_mem0.py
"""
import sys
import json
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("migrate")


def export_mem0():
    """从 Mem0 导出所有记忆。"""
    try:
        from src.memory import _mem0, _USER_ID, init_memory
        if _mem0 is None:
            init_memory()
            from src.memory import _mem0
        all_memories = _mem0.get_all(filters={"user_id": _USER_ID})
        return all_memories if all_memories else []
    except Exception as e:
        logger.warning("导出 Mem0 失败: %s，跳过迁移", e)
        return []


def _gen_id(text: str) -> str:
    return f"mem0_{int(time.time() * 1000)}_{abs(hash(text)) % 1000000}"


def convert(text: str) -> dict:
    """Mem0 字符串记忆 → 新引擎 items schema。"""
    ts = int(time.time())
    return {
        "id": _gen_id(text),
        "resource_id": "",
        "type": "context",
        "content": text,
        "tag": "",
        "grade": "B",
        "keywords": "[]",
        "timestamp": ts,
        "created_at": ts,
        "updated_at": ts,
        "access_count": 0,
        "last_access": 0,
    }


def _map_grade(grade) -> str:
    g = str(grade).upper()
    if g in ("A", "S", "HIGH"):
        return "A"
    if g in ("C", "LOW"):
        return "C"
    return "B"


def _parse_ts(item: dict) -> int:
    ts = item.get("created_at") or item.get("timestamp")
    if ts is None:
        return int(time.time())
    if isinstance(ts, str):
        try:
            from datetime import datetime
            return int(datetime.fromisoformat(ts).timestamp())
        except Exception:
            return int(time.time())
    return int(ts)


def migrate():
    logger.info("=== 白槿记忆迁移：Mem0 → 新引擎 ===")
    logger.info("[1/4] 导出 Mem0 记忆...")
    old_items = export_mem0()
    logger.info("  找到 %s 条旧记忆", len(old_items))

    if not old_items:
        logger.info("  无旧数据，跳过迁移")
        return

    converted = [convert(it) for it in old_items if isinstance(it, str) and it.strip()]
    logger.info("[2/4] 转换完成，%s 条有效", len(converted))

    from memory_engine.db import Database
    from memory_engine.index_time import TimeIndex
    from memory_engine.index_bm25 import BM25Index
    from memory_engine.index_associative import AssociativeIndex
    from memory_engine.index_vector import VectorIndex

    db = Database()
    ti = TimeIndex()
    bm25 = BM25Index()
    ai = AssociativeIndex()
    vi = VectorIndex()

    logger.info("[3/4] 写入新引擎...")
    vi_items = []
    for item in converted:
        db.insert_item(item)
        ti.add(item["id"], item["timestamp"])
        bm25.add(item["id"], item["content"])
        kws = item.get("keywords", [])
        if isinstance(kws, str):
            try:
                kws = json.loads(kws)
            except Exception:
                kws = []
        ai.add(item["id"], kws)
        vi_items.append((item["id"], item["content"]))

    vi.rebuild(vi_items)
    id_map = vi.save_snapshot()
    if id_map:
        db.save_id_mapping(id_map)

    logger.info("[4/4] 迁移完成！")
    logger.info("  总记忆: %s 条", len(converted))
    logger.info("  向量索引: %s 条", len(vi))
    logger.info("  时间轴: %s 条", len(ti))
    logger.info("  BM25: %s 篇", len(bm25))
    db.close()


if __name__ == "__main__":
    migrate()
