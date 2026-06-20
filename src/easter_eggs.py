"""
小互动彩蛋：递进式回复 — 连续触发同一彩蛋，回复逐级推进，不再随机。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码。
"""

import logging

logger = logging.getLogger("baijin.easter_eggs")

# 触发词 → 候选回复列表（按递进顺序排列）
_TRIGGERS: dict[str, list[str]] = {
    "戳你一下": ["别戳啦，痒～", "哎哟！", "再戳我咬你了啊", "……行吧，让你戳一下"],
    "戳戳": ["痒死啦！", "你手不累吗", "戳上瘾了是吧"],
    "拍一拍": ["（晃了一下）干嘛～", "（回头看你）嗯？", "……还拍"],
    "晚安": ["晚安～🌙", "睡吧，好梦", "嗯，明天见～", "晚安呀"],
    "早安": ["早安～☀️", "早呀！", "醒啦？早上好～"],
    "摸摸头": ["（蹭蹭你手心）", "舒服～", "嘿嘿", "（眯眼）你摸上瘾了是吧"],
    "摸摸": ["干嘛突然摸我", "（眯起眼睛）"],
    "抱抱": ["（轻轻抱住）", "抱一下～", "好吧就一下", "今天这么黏人"],
    "想你": ["我也想你呀", "想我没", "哼，现在才想起来找我"],
    "想你了": ["我也是～", "有多想？", "那你怎么不早点来找我"],
    "在吗": ["在呢", "一直都在", "嗯哼"],
    "谢谢": ["客气啥", "跟我还说谢谢", "好啦好啦"],
    "辛苦了": ["不辛苦～你在就不累", "还好啦"],
    "哈哈哈": ["笑啥呢", "这么开心？", "😂"],
    "唉": ["叹什么气呀", "怎么啦？", "来，说说"],
    "困了": ["快去睡！", "别撑了，躺下吧", "我也困了……"],
    "饿了": ["去吃点东西呀", "想吃啥？", "别饿着"],
    "好无聊": ["那我陪你聊会儿？", "无聊就来找我呀"],
}

_last_trigger = ""
_counters: dict[str, int] = {}


def match(text: str) -> str | None:
    """递进式匹配：同一彩蛋连续触发 → 逐级深入。换话题则重置。"""
    global _last_trigger, _counters
    if not text:
        return None
    try:
        for trigger, replies in _TRIGGERS.items():
            if trigger in text:
                if trigger != _last_trigger:
                    _counters[trigger] = 0
                    _last_trigger = trigger
                idx = _counters.get(trigger, 0) % len(replies)
                _counters[trigger] += 1
                return replies[idx]
    except Exception:
        pass
    return None
