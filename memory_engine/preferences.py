"""
偏好提取。自动从对话中识别顺航的偏好，追加到 Markdown 文件。
去重 + 白名单过滤，纯规则驱动。
"""
import re
from .config import CATEGORIES_DIR

PREFERENCES_FILE = CATEGORIES_DIR / "preferences.md"
CATEGORIES_DIR.mkdir(parents=True, exist_ok=True)

# 有效偏好的触发词
_KEYWORDS = ["我喜欢", "我讨厌", "我不喜欢", "我习惯", "我不习惯",
             "希望你", "以后", "最好", "不要总是", "不要老是"]
# 口语化误匹配：这些短语明显不是偏好陈述
_BLACKLIST_PATTERNS = [
    r"^不要走$", r"^不要啊$", r"^不要停$", r"^不要了$",
    r"^不要哭$", r"^不要闹$", r"^不要生气$", r"^不要担心$",
    r"^不要紧$", r"^你觉得呢$", r"^我应该$",
]


def auto_extract_preference(user_msg: str) -> bool:
    """检测并追加偏好，带去重和白名单过滤。"""
    if not any(kw in user_msg for kw in _KEYWORDS):
        return False
    if _is_noise(user_msg):
        return False

    line = re.sub(r"[。！？，、]*$", "", user_msg.strip())
    if _already_exists(line):
        return False

    try:
        with open(PREFERENCES_FILE, "a", encoding="utf-8") as f:
            f.write(f"- {line}\n")
        return True
    except OSError:
        return False


def _is_noise(text: str) -> bool:
    """过滤掉口语化误匹配，不是偏好陈述的句子。"""
    for pattern in _BLACKLIST_PATTERNS:
        if re.match(pattern, text.strip()):
            return True
    if len(text.strip()) < 4:
        return True
    return False


def _already_exists(line: str) -> bool:
    """检查偏好是否已存在，避免重复写入。"""
    if not PREFERENCES_FILE.exists():
        return False
    try:
        with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
            return line in f.read()
    except OSError:
        return False


def analyze_reaction(user_reply: str, previous_reply: str) -> str | None:
    """
    分析用户对上一轮回复的反应，推断隐式偏好。
    不需要用户明确说出来。
    """
    positive = [
        "哈哈", "好棒", "喜欢", "太对了", "哈哈哈", "笑死", "可爱",
        "好耶", "对呀", "没错", "太棒了", "爱了", "绝了",
    ]
    negative = [
        "哦", "嗯", "好吧", "随便", "哦好吧", "...",
        "额", "行吧", "哦行", "嗯好", "就这样", "知道了",
    ]

    if any(k in user_reply for k in positive):
        return f"[隐式] 顺航喜欢这种说话方式 → {previous_reply[:30]}"
    if any(k in user_reply for k in negative):
        return f"[隐式] 顺航不太喜欢这种说话方式 → {previous_reply[:30]}"
    return None


def add_implicit_preference(line: str) -> None:
    """追加隐式偏好，带去重。"""
    if _already_exists(line):
        return
    try:
        with open(PREFERENCES_FILE, "a", encoding="utf-8") as f:
            f.write(f"- {line}\n")
    except OSError:
        pass


def get_preferences() -> str:
    """读取最近30条偏好，避免prompt过长。"""
    if not PREFERENCES_FILE.exists():
        return ""

    try:
        with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return ""

    if not lines:
        return ""

    recent = lines[-30:]
    content = "".join(recent).strip()
    if not content:
        return ""
    return "【顺航的偏好】\n" + content
