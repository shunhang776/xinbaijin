"""
FastAPI 全局异常处理：所有异常返回 QQ Bot ACK + 兜底回复，永不崩溃。

铁律：每函数 ≤ 20 行，先处理异常，对齐 agent_api.py 原版兜底逻辑。
"""

import json
import logging
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger("baijin.errors")

# 兜底回复（与 agent_api.py 完全一致）
FALLBACK_REPLY = "嗯…我暂时说不了话。"
# QQ Bot 平台要求的 ACK
_ACK = json.dumps({"op": 12}, ensure_ascii=False)


# ---------- 全局异常处理器 ----------
async def global_exception_handler(request: Request, exc: Exception):
    """
    捕获所有未处理的异常。
    返回 QQ Bot ACK，确保平台不重复投递消息。
    """
    logger.error(f"未捕获异常 [{type(exc).__name__}]: {exc}", exc_info=True)
    # 如果请求体能解析，尝试提取 openid 发送兜底回复
    try:
        body = await request.body()
        data = json.loads(body)
        openid = data.get("d", {}).get("author", {}).get("user_openid", "")
        if openid:
            await _send_fallback(openid)
    except Exception:
        pass
    return Response(content=_ACK, media_type="application/json")


# ---------- HTTPException 处理器 ----------
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTP 异常 → QQ Bot ACK，避免暴露错误细节。"""
    logger.warning(f"HTTP异常 [{exc.status_code}]: {exc.detail}")
    return Response(content=_ACK, media_type="application/json")


# ---------- JSON 解析异常处理器 ----------
async def json_parse_handler(request: Request, exc: Exception):
    """JSON 解析失败 → ACK，不返回 400 给 QQ 平台。"""
    logger.warning(f"JSON解析失败: {exc}")
    return Response(content=_ACK, media_type="application/json")


# ---------- 兜底消息发送 ----------
async def _get_qq_token(app_id: str, secret: str) -> str:
    """获取 QQ Bot access_token。失败返回空字符串。"""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://bots.qq.com/app/getAppAccessToken",
                json={"appId": app_id, "clientSecret": secret})
        if r.status_code == 200:
            return r.json().get("access_token", "")
        return ""
    except Exception:
        return ""


async def _send_fallback(openid: str):
    """发送兜底回复给用户。失败静默。"""
    try:
        import os, httpx, random
        app_id = os.getenv("QQ_APP_ID", "")
        secret = os.getenv("QQ_APP_SECRET", "")
        token = await _get_qq_token(app_id, secret)
        if not token:
            return
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"https://api.sgroup.qq.com/v2/users/{openid}/messages",
                headers={"Authorization": f"QQBot {token}",
                         "X-Union-Appid": app_id,
                         "Content-Type": "application/json"},
                json={"content": FALLBACK_REPLY, "msg_type": 0,
                      "msg_seq": random.randint(1, 99999999)})
            r.raise_for_status()
    except Exception:
        pass


# ---------- 注册到 FastAPI app ----------
def register_handlers(app):
    """注册所有异常处理器到 FastAPI 实例。"""
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(ValueError, json_parse_handler)
    app.add_exception_handler(Exception, global_exception_handler)
    logger.info("全局异常处理器已注册")
