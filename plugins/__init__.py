"""
白槿插件层 — 五层架构的最上层。

职责：编排记忆检索、工具调用、社交直觉、情绪反应等高层功能。
铁律：插件层只调用类别层和检索接口，绝不跨层直接操作索引层或资源层。

插件清单：
    recall     — 记忆召回（语义+关键词+时间+关联四路并行）
    remember   — 记忆写入（异步流水线，含偏好提取和情绪落盘）
    forget     — 记忆软删除（A/S 级保护）
    search     — 联网搜索（SerpApi + 安全过滤）
    remind     — 提醒管理（A 级持久化）
    intuition  — 社交直觉（FAISS 匹配 + LLM 兜底）
    emotion    — 情绪快照持久化
    identity   — 身份规则自更新
    reflect    — 对话后经验自复盘
"""

# 工具系统（src/tools.py — 给 LLM 的 function calling 工具）
from src.tools import TOOLS, execute as run_tool

# 社交直觉（插件层专属：检索+LLM 双路）
from memory_engine.common_sense import (
    get_social_intuition,
    reflect_on_reply,
    encode_text,
)

# 情绪管理
from memory_engine.emotion import (
    get_recent_emotions,
    save_emotion_snapshot,
)

# 提醒系统
from memory_engine.reminder import (
    get_due_reminders,
    save_reminder,
    mark_reminder_done,
)

# 联网搜索
from src.internet import search as search_internet
from src.safety import filter_result as filter_search_result

# 身份管理
from src.identity import load as load_identity, get_identity, reload as reload_identity

__all__ = [
    "TOOLS", "run_tool",
    "get_social_intuition", "reflect_on_reply", "encode_text",
    "get_recent_emotions", "save_emotion_snapshot",
    "get_due_reminders", "save_reminder", "mark_reminder_done",
    "search_internet", "filter_search_result",
    "load_identity", "get_identity", "reload_identity",
]
