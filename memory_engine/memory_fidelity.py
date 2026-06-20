"""
人类记忆衰减模型。模拟艾宾浩斯遗忘曲线。
A级永久记忆不受任何衰减和模糊影响。
"""
import random

# 艾宾浩斯遗忘曲线参数
_DECAY = {1: 1.0, 3: 0.9, 7: 0.7, 30: 0.4, 90: 0.15, 365: 0.05}
_MAX_BLUR_RATIO = 0.5


def get_fidelity(days: float) -> float:
    """根据天数返回记忆保真度 0-1，区间线性插值。"""
    if days < 1:
        return 1.0
    thresholds = sorted(_DECAY.keys())
    for i, t in enumerate(thresholds):
        if days < t:
            prev_t = thresholds[i - 1] if i > 0 else 0
            prev_f = _DECAY[prev_t] if i > 0 else 1.0
            cur_f = _DECAY[t]
            ratio = (days - prev_t) / (t - prev_t) if t > prev_t else 0
            return prev_f + (cur_f - prev_f) * ratio
    return _DECAY.get(365, 0.05)


def should_remember(days: float) -> bool:
    """概率性遗忘：保真度越低，越可能完全想不起来。"""
    return random.random() < get_fidelity(days)


def blur_text(text: str, fidelity: float) -> str:
    """根据保真度模糊文本，最多模糊 50% 的内容。"""
    if fidelity >= 0.9 or not text or len(text) < 3:
        return text

    blur_ratio = min(1.0 - fidelity, _MAX_BLUR_RATIO)
    blur_count = int(len(text) * blur_ratio)
    if blur_count <= 0:
        return text

    chars = list(text)
    # 随机选位置替换为 ...，避免连续模糊
    indices = random.sample(range(len(chars)), blur_count)
    for i in indices:
        chars[i] = "..."

    result = "".join(chars)
    while "......" in result:
        result = result.replace("......", "...")
    while "...." in result:
        result = result.replace("....", "...")
    return result.strip(". ")


def fidelity_note(fidelity: float) -> str:
    """根据保真度返回自然的状态注释。"""
    if fidelity >= 0.9:
        return ""
    if fidelity >= 0.7:
        return "（有点模糊）"
    if fidelity >= 0.4:
        return "（记得不太清楚了）"
    return "（只有一点印象）"
