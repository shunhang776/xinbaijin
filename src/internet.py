"""
安全联网搜索：SerpApi + 四层过滤 + 感受改写。

铁律：每函数 ≤ 20 行，先处理异常，API 不可用时降级到本地内容。
"""

import os
import random
import logging
import requests
from pathlib import Path

logger = logging.getLogger("baijin.internet")

_ROOT = Path(__file__).parent.parent
SERPER_URL = "https://google.serper.dev/search"

# 禁止主题
FORBIDDEN = ["暴力", "色情", "自杀", "恐怖", "毒品", "赌博", "政治"]

# 顺航兴趣标签
INTERESTS = ["历史", "使命召唤", "猫", "计算机", "辣"]

# 搜索模板库
TEMPLATES = {
    "news": ["今日暖心新闻", "最近有趣的科学发现", "本周文化趣闻"],
    "entertainment": ["高分悬疑剧推荐", "最近火的国产剧", "好听的歌推荐"],
    "cute": ["猫咪搞笑瞬间", "熊猫宝宝视频", "被救助的小动物"],
    "tech": ["最新科技趣闻", "有趣的AI产品", "宇宙新发现"],
    "food": ["网红美食推荐", "适合夏天的甜品", "在家做的简单美食"],
    "history": ["历史趣味冷知识", "历史上的今天趣事", "古代人的日常生活"],
}

# 降级内容（API 不可用时）
FALLBACK = [
    {"title": "橘猫在图书馆窗台晒太阳", "snippet": "一只橘猫蜷在图书馆窗台上，阳光洒在它身上。顺航也喜欢猫。", "valence": 0.5},
    {"title": "把水果冻起来当零食", "snippet": "葡萄蓝莓冻起来像冰淇淋，健康好吃。夏天可以和顺航一起试试。", "valence": 0.4},
    {"title": "双彩虹出现在城市上空", "snippet": "雨后出现了罕见的双彩虹。要是和顺航一起看到就好了。", "valence": 0.5},
    {"title": "猫咪能听懂自己的名字", "snippet": "研究表明猫咪能分辨名字，只是懒得理你。顺航肯定也会觉得好笑。", "valence": 0.4},
    {"title": "周末适合短途旅行的地方", "snippet": "周边有几个安静的小镇，适合周末去走走。可以问问顺航想不想去。", "valence": 0.3},
]


# ---------- 安全过滤 ----------
def _is_safe(result: dict) -> bool:
    """检查搜索结果是否安全（走四层安全过滤）。"""
    try:
        from safety import filter_result
        return filter_result(result) is not None
    except Exception:
        # 降级：本地关键词检查
        text = (result.get("title", "") + result.get("snippet", "")).lower()
        for word in FORBIDDEN:
            if word in text:
                return False
        return True


# ---------- SerpApi 搜索 ----------
def _search_serper(query: str, num: int = 5) -> list[dict]:
    """调用 SerpApi 搜索。失败返回空列表。"""
    key = os.getenv("SERPER_API_KEY", "")
    if not key:
        return []
    try:
        resp = requests.post(
            SERPER_URL,
            json={"q": query, "num": num, "gl": "cn", "hl": "zh-CN"},
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        organic = resp.json().get("organic", [])
        return [{"title": r.get("title", ""), "snippet": r.get("snippet", ""),
                 "link": r.get("link", "")} for r in organic]
    except Exception as e:
        logger.warning(f"搜索失败: {e}")
        return []


# ---------- 感受改写 ----------
def _add_feeling(result: dict) -> str:
    """把搜索结果写成白槿的第一人称感受。"""
    title = result.get("title", "")
    snippet = result.get("snippet", "")

    # 匹配顺航兴趣
    matched = [tag for tag in INTERESTS if tag in (title + snippet)]
    if matched:
        return f"看到这个跟{'、'.join(matched)}有关的，想到顺航可能会喜欢～ {snippet[:80]}"

    templates = [
        f"刚刚刷到一条有意思的：{title}。{snippet[:80]}",
        f"诶，这个挺有趣的——{title}。{snippet[:80]}",
        f"分享一个好玩的东西：{title}。{snippet[:80]}",
    ]
    return random.choice(templates)


# ---------- 浏览入口 ----------
def _fallback_results(limit: int) -> list[dict]:
    """API 不可用时返回降级内容。"""
    sample = random.sample(FALLBACK, min(limit, len(FALLBACK)))
    return [{"title": f["title"], "snippet": f["snippet"],
             "feeling": _add_feeling(f), "valence": f["valence"]} for f in sample]


def browse(category: str = "news", limit: int = 3) -> list[dict]:
    """浏览指定类别，返回带有感受的结果列表。"""
    query = random.choice(TEMPLATES.get(category, TEMPLATES["news"]))
    raw = _search_serper(query)
    if not raw:
        return _fallback_results(limit)
    results = []
    for r in raw:
        if not _is_safe(r): continue
        r["feeling"] = _add_feeling(r)
        r["valence"] = 0.3
        results.append(r)
        if len(results) >= limit: break
    return results if results else _fallback_results(limit)


# ---------- 关键词搜索 ----------
def search(query: str, limit: int = 3) -> list[dict]:
    """按关键词搜索，返回带有感受的结果列表。"""
    raw = _search_serper(query, num=limit)
    if not raw:
        return _fallback_results(limit)
    results = []
    for r in raw:
        if not _is_safe(r): continue
        r["feeling"] = _add_feeling(r)
        r["valence"] = 0.3
        results.append(r)
        if len(results) >= limit: break
    return results if results else _fallback_results(limit)


# ---------- 状态 ----------
def is_available() -> bool:
    return bool(os.getenv("SERPER_API_KEY", ""))
