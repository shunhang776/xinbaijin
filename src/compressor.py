"""
记忆压缩：LLM 驱动 → 多轮对话 → 单条摘要。节省存储，提升检索精度。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码。
"""

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("baijin.compressor")
_BEIJING = timezone(timedelta(hours=8))


async def _summarize(memories: list[str]) -> str:
    """用 LLM 将多条记忆压缩为一条摘要。"""
    text = "\n".join([f"- {m}" for m in memories])
    prompt = ("把以下对话记录压缩成一句中文摘要，保留关键信息（人物、事件、情绪、日期），"
              f"去掉冗余细节：\n{text}\n\n摘要：")
    from deepseek import generate
    result = await generate(
        [{"role": "user", "content": prompt}],
        model="deepseek-chat", max_tokens=150, temperature=0.3)
    return result.strip() if result else ""


def _del(fn, mid):
    try: fn(mid)
    except Exception:
        logger.debug("压缩删除失败: %s", mid)


def _is_old(memory: dict, days: int = 3) -> bool:
    """判断记忆是否超过指定天数。"""
    try:
        ts = memory.get("metadata", {}).get("created_at") or memory.get("created_at")
        if not ts:
            return False
        created = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (datetime.now(_BEIJING) - created).days >= days
    except Exception:
        return False


async def compress(store_fn, search_fn, delete_fn) -> dict:
    """扫描旧记忆 → LLM 摘要 → 存新删旧。返回统计。"""
    try:
        all_mems = search_fn("", top_k=100)
        old = [m for m in all_mems
               if _is_old(m) and m.get("metadata", {}).get("type") != "compressed_summary"]
        if len(old) < 3: return {"compressed": 0, "reason": "不足3条，跳过"}
        s = await _summarize([m["content"] for m in old])
        if not s or len(s) < 5: return {"compressed": 0, "reason": "摘要过短"}
        store_fn(f"[{datetime.now(_BEIJING).strftime('%m月%d日')}前] {s}",
                 {"type": "compressed_summary", "source_count": len(old)})
        for m in old: _del(delete_fn, m["id"])
        logger.info(f"压缩完成: {len(old)} 条 → 1 条摘要")
        return {"compressed": len(old), "summary": s[:100]}
    except Exception as e:
        logger.warning(f"记忆压缩失败: {e}")
        return {"error": str(e)}
