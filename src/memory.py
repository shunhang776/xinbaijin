"""
白槿记忆模块 — 统一入口，已委托给自研五层全栈引擎。

本文件为向后兼容保留层，所有调用透明转发到 MemoryEngine。
新代码请直接使用:
    from memory_engine import get_engine
"""
import logging

logger = logging.getLogger("baijin.memory")


def _get_engine():
    """懒加载引擎单例。"""
    from memory_engine import get_engine
    return get_engine()


# ═══════════════════════════════════════════
# 公开 API（与原 Mem0 版 100% 签名兼容）
# ═══════════════════════════════════════════

def init_memory(embed_function=None) -> bool:
    """初始化记忆引擎。新引擎懒加载，此函数仅为兼容保留。"""
    try:
        _get_engine()
        return True
    except Exception as e:
        logger.error(f"记忆引擎初始化失败: {e}")
        return False


def store(text: str, metadata: dict = None) -> bool:
    """存储记忆。自动解析对话格式或作为系统记忆写入。"""
    if not text or not text.strip():
        return False
    if metadata is None:
        metadata = {}
    try:
        engine = _get_engine()
        # 解析对话格式 "顺航：xxx\n白槿：xxx"，其余作为系统消息
        if "\n白槿：" in text:
            parts = text.split("\n白槿：", 1)
            user_msg = parts[0].replace("顺航：", "", 1) if parts[0].startswith("顺航：") else parts[0]
            assistant_reply = parts[1] if len(parts) > 1 else ""
        elif text.startswith("顺航："):
            user_msg = text.replace("顺航：", "", 1)
            assistant_reply = ""
        else:
            user_msg = ""
            assistant_reply = text
        emotion = metadata.get("emotion")
        event = metadata.get("event")
        engine.remember_sync(user_msg, assistant_reply,
                             cloud_snapshot=emotion, emotion_event=event)
        return True
    except Exception as e:
        logger.warning(f"记忆存储失败: {e}")
        return False


def search(query: str, top_k: int = None) -> list[dict]:
    """语义检索最相关记忆。兼容原 Mem0 返回值结构。"""
    if not query or not query.strip():
        return []
    try:
        engine = _get_engine()
        results = engine.search_sync(query)
        if top_k is not None:
            results = results[:top_k]
        # 转换为原 Mem0 兼容格式
        return [
            {
                "id": r.get("id", ""),
                "content": r.get("content", ""),
                "metadata": {
                    "type": r.get("type", ""),
                    "grade": r.get("grade", ""),
                    "keywords": r.get("keywords", []),
                    "timestamp": r.get("timestamp", 0),
                    "score": r.get("_score", 0.0),
                },
                "score": r.get("_score", 0.0),
            }
            for r in results
        ]
    except Exception as e:
        logger.warning(f"记忆检索失败: {e}")
        return []


def recall(query: str) -> str:
    """语义回忆，格式化返回最近记忆。"""
    results = search(query, top_k=10)
    if not results:
        return "没有找到相关记忆。"
    # 高优先级排前面（新引擎 grade A/S 为高优先级）
    results.sort(key=lambda r: (
        0 if r.get("metadata", {}).get("grade") in ("A", "S") else 1,
        -(r.get("score", 0.0))
    ))
    return "\n".join([f"[{r['id'][:8]}] {r['content']}" for r in results[:5]])


def delete(memory_id: str) -> bool:
    """删除单条记忆（软删除）。A/S 级记忆受保护不可删除。"""
    try:
        engine = _get_engine()
        engine.db.soft_delete(memory_id)
        return True
    except Exception as e:
        logger.warning(f"删除记忆失败: {e}")
        return False


def count() -> int:
    """获取记忆总数。"""
    try:
        engine = _get_engine()
        items = engine.db.load_all_items()
        return len(items)
    except Exception:
        return 0


def clear() -> bool:
    """清空所有记忆（新引擎不支持批量清空，需逐条删除）。"""
    logger.warning("clear() 在新引擎中不支持批量操作，请逐条删除")
    return False


def is_ready() -> bool:
    """检查记忆引擎是否就绪。"""
    try:
        _get_engine()
        return True
    except Exception:
        return False
