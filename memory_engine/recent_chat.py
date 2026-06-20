"""
终端专属短期对话缓存。从引擎 items 表读取最近对话，替代 conversation.py。
"""
from .utils import now_ts


def get_recent_chat(db, n: int = 8) -> list[dict]:
    """获取最近 n 条对话，返回 {role, content} 列表。"""
    rows = db.query(
        "SELECT content FROM items WHERE type IN ('context','chat','preference') "
        "AND deleted = 0 ORDER BY timestamp DESC LIMIT ?",
        (n,)
    )
    if not rows:
        return []

    messages = []
    for r in reversed(rows):
        text = r["content"]
        if text.startswith("用户："):
            messages.append({"role": "user", "content": text[3:]})
        elif text.startswith("白槿："):
            messages.append({"role": "assistant", "content": text[3:]})
        else:
            messages.append({"role": "system", "content": text})
    return messages


def format_recent_chat(db, n: int = 6) -> str:
    """格式化最近对话为 prompt 可用的文本。"""
    msgs = get_recent_chat(db, n)
    if not msgs:
        return ""

    lines = ["【刚才的对话】"]
    for m in msgs:
        role = "顺航" if m["role"] == "user" else "白槿" if m["role"] == "assistant" else "系统"
        lines.append(f"{role}：{m['content']}")
    return "\n".join(lines)
