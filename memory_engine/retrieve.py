"""
记忆检索 + 格式化。时间衰减 + 模糊化，模拟人类记忆。
A级永久记忆不受任何衰减影响，永远清晰。
"""
import time
import random
from .engine import _engine as memory_engine
from .memory_fidelity import get_fidelity, should_remember, blur_text, fidelity_note


def get_relevant_memories(query: str,
                         prefetched: list[dict] | None = None,
                         query_vec=None) -> list[dict]:
    """
    检索后按时间衰减 + 记忆等级排序，返回 1-3 条。
    A级永久记忆：100%返回，永远清晰，最多2条。
    普通记忆：按艾宾浩斯曲线概率遗忘，越久越模糊。

    prefetched: 可选，预检索的候选记忆列表。传入后跳过 search_sync，
                复用 MemoryBackgrounder 的检索结果，省一次 FAISS 查询。
    query_vec: 可选，预编码向量，透传给 search_sync 避免子线程 BGE 调用。
    """
    if memory_engine is None:
        return []

    if prefetched is not None:
        results = prefetched
    else:
        results, _ = memory_engine.retrieve.search_sync(query, query_vec=query_vec)
    now = time.time()
    a_items = []
    normal_items = []
    seen_ids: set[str] = set()

    for item in results:
        iid = item.get("id")
        if not iid or iid in seen_ids:
            continue
        seen_ids.add(iid)

        # 情绪记忆不占用展示名额，由 associated_emotions 关联到事实
        if item.get("type") == "emotion":
            continue

        if item.get("grade") == "A":
            item["_fidelity"] = 1.0
            a_items.append(item)
            continue

        ts = item.get("timestamp", 0)
        if not ts:
            normal_items.append(item)
            continue

        days = (now - ts) / 86400
        if should_remember(days):
            item["_fidelity"] = get_fidelity(days)
            normal_items.append(item)

    # A级记忆按时间倒序排列，最近的优先
    a_items.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

    # 普通记忆按保真度降序排序，高保真（更清晰）的优先展示
    normal_items.sort(key=lambda x: x.get("_fidelity", 0), reverse=True)

    # 最终展示 1-3 条，A 级优先
    total = random.randint(1, 3)
    final = a_items[:2]
    remaining = total - len(final)
    if remaining > 0:
        final += normal_items[:remaining]
    return final


_EMOJI_MAP = {
    "happy": "\U0001F60A", "sad": "\U0001F622", "angry": "\U0001F620",
    "anxious": "\U0001F630", "proud": "✨", "tender": "\U0001F49B",
    "confused": "\U0001F914", "longing": "\U0001F4AD", "neutral": "",
}


def _emotion_prefix(content: str, intensity: int = 3) -> str:
    """根据情绪内容和强度选自然前缀。内容已有前缀时不重复。"""
    if any(p in content for p in ["那时候", "当时", "记得那时候"]):
        return ""
    strong = intensity >= 4
    if "开心" in content or "高兴" in content or "笑了" in content:
        return "那时候特别开心，" if strong else "那时候挺高兴的，"
    if "难过" in content or "悲伤" in content or "哭了" in content:
        return "那时候特别难过，" if strong else "那时候有点难过，"
    if "烦躁" in content or "烦死了" in content or "生气" in content:
        return "那时候烦躁极了，" if strong else "那时候有点烦，"
    if "成就感" in content or "搞定了" in content or "终于" in content:
        return "那时候特有成就感，" if strong else "那时候挺有成就感的，"
    if "担心" in content or "焦虑" in content or "害怕" in content:
        return "那时候担心得不行，" if strong else "那时候有点担心，"
    if "心疼" in content:
        return "那时候心疼坏了，" if strong else "那时候挺心疼的，"
    if "低落" in content:
        return "那时候情绪特别低落，" if strong else "那时候情绪有点低落，"
    if any(w in content for w in ["迷茫", "困惑", "惊喜", "感动", "温暖", "想念", "期待", "失落"]):
        return "那时候"
    return ""


def format_memory_for_prompt(memories: list[dict], sender_type: str = "qq") -> str:
    """格式化记忆。情绪由 retrieve_core 预先关联到 associated_emotion 字段。
    sender_type 非 qq 时屏蔽 emoji，只保留纯文字。"""
    if not memories:
        return "【你不太记得相关的事情了】"

    chat_lines = []
    fact_lines = []

    for mem in memories:
        ts = mem.get("timestamp", 0)
        mem_type = mem.get("type", "")
        fid = mem.get("_fidelity", 1.0)
        permanent = mem.get("grade") == "A"

        if mem_type == "emotion":
            continue  # 情绪不单独展示

        if mem_type == "category":
            fact_lines.append(f"- {mem.get('content', '')}")
            continue

        if ts:
            tstr = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
            note = "" if permanent else fidelity_note(fid)
            chat_lines.append(f"{tstr} {note}".strip())

        if mem_type == "chat":
            u = mem.get("user_msg", "")
            a = mem.get("assistant_reply", "")
            if not permanent:
                u = blur_text(u, fid)
                a = blur_text(a, fid)
            if u:
                chat_lines.append(f"顺航：{u}")
            if a:
                chat_lines.append(f"白槿：{a}")
        else:
            c = mem.get("content", "")
            if c:
                if not permanent:
                    c = blur_text(c, fid)
                chat_lines.append(c)

        emotions = mem.get("associated_emotions", [])
        show_emoji = sender_type == "qq"
        if len(emotions) == 1:
            e = emotions[0]
            try:
                content = e if isinstance(e, str) else e["content"]
                etype = e.get("type", "") if isinstance(e, dict) else ""
                intensity = 3 if isinstance(e, str) else e.get("intensity", 3)
            except Exception:
                content, etype, intensity = "", "", 3
            emoji = _EMOJI_MAP.get(etype, "") if show_emoji else ""
            prefix = _emotion_prefix(content, intensity)
            spacer = " " if emoji else ""
            chat_lines.append(f"  {emoji}{spacer}{prefix}{content}")
        elif len(emotions) > 1:
            chat_lines.append("  当时的心情：")
            for e in emotions:
                try:
                    content = e if isinstance(e, str) else e["content"]
                    etype = e.get("type", "") if isinstance(e, dict) else ""
                    intensity = 3 if isinstance(e, str) else e.get("intensity", 3)
                except Exception:
                    content, etype, intensity = "", "", 3
                emoji = _EMOJI_MAP.get(etype, "") if show_emoji else ""
                spacer = " " if emoji else ""
                prefix = _emotion_prefix(content, intensity)
                chat_lines.append(f"    - {emoji}{spacer}{prefix}{content}")
        chat_lines.append("")

    result = ""
    if chat_lines:
        result = "【你能想起的相关对话】\n" + "\n".join(chat_lines).strip()
    if fact_lines:
        result += "\n\n【你知道的事情】\n" + "\n".join(fact_lines).strip()
    return result.strip()
