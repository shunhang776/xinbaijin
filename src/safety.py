"""
四层联网安全过滤：关键词 → 域名白名单 → 敏感内容检测 → 摘要截断。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码。
"""

import re
import logging
from urllib.parse import urlparse

logger = logging.getLogger("baijin.safety")

_BLOCKED_KEYWORDS = [
    "暴力", "血腥", "色情", "成人", "裸", "性", "政治敏感",
    "自杀", "恐怖", "谋杀", "毒品", "赌博", "暗网", "黑客攻击",
    "枪支", "爆炸", "虐", "死亡", "尸体",
]

_TRUSTED_DOMAINS = [
    "bbc.com", "people.com.cn", "xinhuanet.com", "thepaper.cn",
    "jiemian.com", "36kr.com", "douban.com", "zhihu.com",
    "huxiu.com", "geekpark.net", "bilibili.com",
]

_SENSITIVE_PATTERNS = [
    r"(广告|推广|加微信|扫码|点击购买|免费领取|限时优惠)",
    r"(赌博|彩票|下注|赌场|博彩)",
    r"(贷款|借款|利息|放款|信用卡套现)",
]


# ---------- Layer 1: 关键词 ----------
def _check_keywords(text: str) -> bool:
    for kw in _BLOCKED_KEYWORDS:
        if kw in text:
            logger.info(f"安全拦截(关键词): {kw}")
            return False
    return True


# ---------- Layer 2: 域名白名单 ----------
def _check_domain(url: str) -> bool:
    if not url:
        return True
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        for trusted in _TRUSTED_DOMAINS:
            if domain.endswith(trusted):
                return True
        logger.info(f"安全拦截(域名): {domain}")
        return False
    except Exception:
        return True


# ---------- Layer 3: 敏感内容 ----------
def _check_sensitive(text: str) -> bool:
    for pattern in _SENSITIVE_PATTERNS:
        try:
            if re.search(pattern, text):
                logger.info(f"安全拦截(模式): {pattern[:20]}...")
                return False
        except Exception as e:
            logger.warning(f"正则错误: {pattern}, {e}")
            continue
    return True


# ---------- Layer 4: 摘要截断 ----------
def _truncate(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rfind("。")
    if cut == -1:
        cut = text[:max_len].rfind("，")
    if cut > max_len // 2:
        return text[:cut+1]
    return text[:max_len-3] + "..."


# ---------- 一站式过滤 ----------
def filter_result(result: dict):
    """
    四层过滤。安全返回新 dict，不安全返回 None。
    不修改原 dict。
    """
    if not isinstance(result, dict):
        return None
    title = result.get("title", "")
    snippet = result.get("snippet", "")
    link = result.get("link", "")
    text = title + " " + snippet

    if not _check_keywords(text): return None
    if not _check_domain(link): return None
    if not _check_sensitive(text): return None

    r = dict(result)
    r["snippet"] = _truncate(snippet)
    return r


def is_safe(result: dict) -> bool:
    return filter_result(result) is not None
