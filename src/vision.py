"""
图片理解模块：URL → 视觉模型描述 → 文本。可插拔多种视觉 API。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码。
"""

import os, base64, logging, httpx
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger("baijin.vision")
_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env")

_VISION_URL = os.getenv("VISION_API_URL", "")
_VISION_KEY = os.getenv("VISION_API_KEY", "")
_VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o")
_AVAILABLE = bool(_VISION_URL and _VISION_KEY)


def is_available() -> bool:
    return _AVAILABLE


async def _fetch_image(url: str) -> bytes:
    """下载图片，返回字节。"""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.content


def _guess_mime(data: bytes) -> str:
    """根据文件头判断 MIME 类型。"""
    if data[:4] == b"\x89PNG": return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"): return "image/gif"
    return "image/jpeg"


async def _call_vision(b64: str, mime: str, prompt: str) -> str:
    """调用视觉 API，返回描述文本。"""
    body = {"model": _VISION_MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "text",
                 "text": prompt or "简洁描述这张图片的内容，一两句话。"},
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}], "max_tokens": 200}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(_VISION_URL, json=body,
            headers={"Authorization": f"Bearer {_VISION_KEY}"})
        r.raise_for_status()
        resp = r.json()
        return (resp.get("choices", [{}])[0]
                .get("message", {}).get("content", "").strip())


async def describe(url: str, prompt: str = "") -> str:
    """传入图片 URL，返回文字描述。不可用时返回 URL 标记。"""
    if not _AVAILABLE:
        return _fallback(url)
    try:
        data = await _fetch_image(url)
        size_mb = len(data) / (1024 * 1024)
        if size_mb > 10:
            logger.warning(f"图片过大 ({size_mb:.1f}MB)，降级处理")
            return _fallback(url)
        desc = await _call_vision(
            base64.b64encode(data).decode(), _guess_mime(data), prompt)
        logger.info(f"图片描述 ({size_mb:.1f}MB): {desc[:80]}")
        return desc
    except Exception as e:
        logger.warning(f"图片理解失败: {e}")
        return _fallback(url)


def _fallback(url: str) -> str:
    """无视觉 API 时的降级标记。"""
    return f"[用户发了一张图片: {url[:60]}...]"


def extract_urls(event_data: dict) -> list[str]:
    """从 QQ 消息事件中提取图片/表情/贴图 URL 列表。"""
    urls = []
    try:
        for att in event_data.get("attachments", []) or []:
            ct = att.get("content_type", "").lower()
            u = att.get("url", "")
            if not u:
                continue
            if any(k in ct for k in ("image", "picture", "sticker",
                                       "emoji", "gif", "video")):
                urls.append(u)
    except Exception:
        pass
    return urls


def extract_emoji_text(content: str) -> str:
    """从消息文本中提取 QQ 表情的语义描述。"""
    import re
    emojis = {
        "178": "斜眼笑/狗头", "177": "捂脸", "182": "笑哭",
        "179": "doge/狗头", "76": "赞", "21": "流泪", "14": "笑哭",
        "12": "爱心", "24": "尴尬", "3": "色", "5": "可怜",
        "66": "坏笑", "73": "抠鼻", "84": "白眼", "60": "抠鼻/无语",
        "104": "呲牙", "105": "偷笑", "106": "愉快", "107": "白眼",
        "108": "傲慢", "109": "饥饿", "110": "困", "111": "惊恐",
    }
    def _replace(m):
        eid = m.group(1)
        return emojis.get(eid, "表情")
    # QQ 表情格式: [CQ:face,id=178]
    return re.sub(r"\[CQ:face,id=(\d+)\]", _replace, content)
