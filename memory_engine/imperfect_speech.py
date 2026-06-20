"""
不完美表达模拟。给回复添加人类说话的小瑕疵，概率性触发。
30%概率添加，只对短回复生效，不修改核心语义。
"""
import random
import re


def add_imperfections(text: str) -> str:
    """给回复添加自然的小瑕疵。只改表面，不动语义。"""
    if random.random() > 0.1:
        return text
    if len(text) > 50 or not text.strip():
        return text
    if "…" in text or "..." in text:
        return text

    return random.choice([
        # 轻微口吃：重复开头第一个字
        lambda t: t[0] + "…" + t if len(t) > 1 else t,
        # 句中停顿：标点后面加省略号
        lambda t: re.sub(r"([，。！？])", r"\1…", t, count=1),
        # 句尾语气停顿
        lambda t: t + "…",
        # 轻微打错字（重复一个中文字符）
        lambda t: re.sub(
            r"([一-龥])",
            lambda m: m.group(1) * 2,
            t, count=1
        ),
    ])(text)
