"""
长期习惯学习：LLM 从记忆中识别模式 → 存储 → 到期自然提及。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码。
"""

import json, logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("baijin.habits")
_ROOT = Path(__file__).parent.parent
_DATA_PATH = _ROOT / "data" / "habits.json"
_BEIJING = timezone(timedelta(hours=8))

_habits: list[dict] = []


def _load():
    global _habits
    try:
        if _DATA_PATH.exists():
            with open(_DATA_PATH, "r", encoding="utf-8") as f:
                _habits = json.load(f)
    except Exception:
        _habits = []


def _save():
    try:
        _DATA_PATH.parent.mkdir(exist_ok=True)
        with open(_DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(_habits, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"习惯保存失败: {e}")


def _build_habit_prompt(texts: list[str]) -> str:
    return ("从以下对话记录中找出用户的长期习惯或规律性活动。"
            "每条用「时间规律：描述」格式输出。例如：\n"
            "「每周五：顺航晚上去健身」「每月1号：发工资」\n"
            "没有明显习惯就说「无」。\n\n" + "\n".join([f"- {t}" for t in texts[-80:]]))


def _parse_habits(result: str) -> list[dict]:
    found = []
    for line in (result or "").split("\n"):
        line = line.strip()
        if "：" in line and line[0] != "无":
            pattern, desc = line.split("：", 1)
            found.append({"pattern": pattern, "description": desc,
                          "found_at": datetime.now(_BEIJING).isoformat()})
    return found


def match_today() -> list[dict]:
    """返回匹配今天日期/星期的习惯。"""
    now = datetime.now(_BEIJING)
    weekday_map = ["周一","周二","周三","周四","周五","周六","周日"]
    today_weekday = weekday_map[now.weekday()]
    today_day = f"{now.day}号"
    matched = []
    for h in _habits:
        p = h.get("pattern", "")
        if today_weekday in p or today_day in p:
            matched.append(h)
    return matched


_load()
