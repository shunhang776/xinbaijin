"""
DeepSeek API 客户端：流式输出 + 持久连接 + 指数退避重试 + 错误码细化。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码。
"""

import os
import json
import asyncio
import logging
import httpx

logger = logging.getLogger("baijin.deepseek")

API_URL = "https://api.deepseek.com/v1/chat/completions"
FALLBACK = "嗯…我暂时说不了话。"
RATE_LIMIT_MSG = "哎呀，我有点忙不过来了~"
AUTH_ERROR_MSG = "嗯，我好像出了点小问题..."

# 持久连接池，省去每次 TCP/TLS 握手
_client: httpx.AsyncClient = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        key = os.getenv("DEEPSEEK_API_KEY", "")
        _client = httpx.AsyncClient(
            timeout=45,
            limits=httpx.Limits(max_keepalive_connections=2),
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
        )
    return _client


def _handle_error(e: Exception, attempt: int) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        if e.response.status_code == 429:
            logger.error("API限流(429)")
            return RATE_LIMIT_MSG
        if e.response.status_code == 401:
            logger.error("API密钥无效(401)")
            return AUTH_ERROR_MSG
    if attempt == 2:
        logger.error(f"DeepSeek失败(重试3次): {type(e).__name__}")
        return FALLBACK
    return ""


async def _stream_collect(client, model, messages, max_tokens, temperature) -> str:
    """流式请求 → 收集全部 token。"""
    result = []
    async with client.stream("POST", API_URL,
        json={"model": model, "messages": messages,
              "max_tokens": max_tokens, "temperature": temperature,
              "stream": True}) as resp:
        resp.raise_for_status()
        async for chunk in resp.aiter_text():
            if not chunk.startswith("data: "): continue
            payload = chunk[6:].strip()
            if payload == "[DONE]": break
            try:
                delta = json.loads(payload)
                c = delta.get("choices",[{}])[0].get("delta",{}).get("content","")
                if c: result.append(c)
            except Exception:
                continue
    reply = "".join(result).strip()
    return reply if reply else FALLBACK


async def generate(messages: list[dict], model: str = "deepseek-chat",
                   max_tokens: int = 500, temperature: float = 0.9) -> str:
    """流式调用，首 token ~300ms，持久连接，3 次指数退避重试。"""
    if not messages:
        return FALLBACK
    for attempt in range(3):
        try:
            return await _stream_collect(
                _get_client(), model, messages, max_tokens, temperature)
        except httpx.HTTPStatusError as e:
            r = _handle_error(e, attempt)
            if r: return r
        except Exception as e:
            r = _handle_error(e, attempt)
            if r: return r
        await asyncio.sleep(0.5 * (2 ** attempt))
    return FALLBACK


# ---------- AI Native 工具调用支持 ----------
import json as _json

def generate_with_tools_sync(messages: list[dict], tools: list[dict],
                              executor, model="deepseek-chat",
                              max_turns=5, timeout=5) -> str:
    """纯同步工具调用生成。httpx.Client 直连，不经过 asyncio。"""
    msgs = list(messages)
    key = os.getenv("DEEPSEEK_API_KEY", "")
    for _ in range(max_turns):
        resp = _call_nonstream_sync(msgs, model, key, tools, timeout)
        tcs = _parse_tool_calls(resp)
        if not tcs:
            return resp.get("content", FALLBACK)
        msgs.append({"role": "assistant", "tool_calls": tcs, "content": None})
        for tc in tcs:
            fn = tc.get("function", tc)
            result = executor(fn["name"],
                            _json.loads(fn.get("arguments", "{}")))
            msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                        "content": result})
    resp = _call_nonstream_sync(msgs, model, key, timeout=timeout)
    return resp.get("content", FALLBACK)


def _call_nonstream_sync(messages, model, key, tools=None, timeout=5):
    """同步非流式请求。异常透传给上层做细粒度日志。"""
    body = {"model": model, "messages": messages,
            "max_tokens": 500, "temperature": 0.9}
    if tools:
        body["tools"] = tools
    with httpx.Client(timeout=timeout, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }) as client:
        resp = client.post(API_URL, json=body)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]


async def generate_with_tools(messages: list[dict], tools: list[dict],
                               executor, model="deepseek-chat",
                               max_turns=5) -> str:
    """支持工具调用的生成。LLM 最多调用工具 max_turns 轮。"""
    msgs = list(messages)
    for _ in range(max_turns):
        resp = await _call_nonstream(msgs, model, tools)
        tcs = _parse_tool_calls(resp)
        if not tcs:
            return resp.get("content", FALLBACK)
        msgs.append({"role": "assistant", "tool_calls": tcs, "content": None})
        for tc in tcs:
            fn = tc.get("function", tc)
            result = executor(fn["name"],
                            _json.loads(fn.get("arguments", "{}")))
            msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                        "content": result})
    return (await _call_nonstream(msgs, model)).get("content", FALLBACK)


async def _call_nonstream(messages, model, tools=None):
    """非流式请求，返回完整消息对象。失败返回兜底。"""
    try:
        client = _get_client()
        body = {"model": model, "messages": messages,
                "max_tokens": 500, "temperature": 0.9}
        if tools:
            body["tools"] = tools
        resp = await client.post(API_URL, json=body)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]
    except Exception as e:
        logger.warning(f"非流式请求失败: {e}")
        return {"content": FALLBACK}


def _parse_tool_calls(msg: dict) -> list:
    """从消息对象提取工具调用列表。兼容 DeepSeek/OpenAI 多种格式。"""
    if not msg:
        return []
    tcs = msg.get("tool_calls", [])
    if not tcs:
        return []
    result = []
    for tc in tcs:
        fn = tc.get("function", {})
        result.append({
            "id": tc.get("id", ""),
            "type": tc.get("type", "function"),
            "function": {
                "name": fn.get("name", ""),
                "arguments": fn.get("arguments", "{}")
            }
        })
    return result
