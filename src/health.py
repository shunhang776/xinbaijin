"""
健康监控面板：模块状态 + DeepSeek/QQ 依赖检查 + Debug 模式。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码。
"""

import os
import time
import logging

logger = logging.getLogger("baijin.health")
_start_time = time.time()
DEBUG = os.getenv("BAIJIN_DEBUG", "").lower() in ("1", "true", "yes")


def debug_log(msg: str, level: str = "warning"):
    """Debug 模式：错误直接打印到 stderr。生产模式：仅写日志。"""
    if DEBUG:
        import sys
        print(f"[BAIJIN_DEBUG] {level.upper()}: {msg}", file=sys.stderr)
    getattr(logger, level)(msg)


def _check_module(name: str, import_path: str, check_fn: str) -> bool:
    try:
        mod = __import__(import_path, fromlist=[check_fn])
        return bool(getattr(mod, check_fn)())
    except (ImportError, AttributeError, RuntimeError) as e:
        debug_log(f"模块 {name} 异常: {e}", "error")
        return False


def module_status() -> dict:
    modules = [
        ("embedding",   "embedding",    "is_ready"),
        ("memory",      "memory",       "is_ready"),
        ("identity",    "identity",     "get_identity"),
    ]
    result = {}
    for name, path, fn in modules:
        try:
            mod = __import__(path, fromlist=[fn])
            ok = bool(getattr(mod, fn)())
        except Exception as e:
            debug_log(f"模块 {name} 异常: {e}", "error")
            ok = False
        result[name] = ok
    return result


def resource_usage() -> dict:
    return {"cpu_count": os.cpu_count() or 0,
            "uptime_seconds": round(time.time() - _start_time),
            "debug": DEBUG}


async def _check_deepseek() -> bool:
    try:
        from deepseek import generate
        reply = await generate([{"role": "user", "content": "hi"}], max_tokens=5)
        return bool(reply)
    except Exception as e:
        debug_log(f"DeepSeek 检查失败: {e}", "error")
        return False


async def _check_qq() -> bool:
    try:
        import httpx
        app_id = os.getenv("QQ_APP_ID", "")
        secret = os.getenv("QQ_APP_SECRET", "")
        if not app_id or not secret:
            return False
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post("https://bots.qq.com/app/getAppAccessToken",
                json={"appId": app_id, "clientSecret": secret})
            return r.status_code == 200
    except Exception as e:
        debug_log(f"QQ 检查失败: {e}", "error")
        return False


def register_health_route(app: "FastAPI"):
    @app.get("/health")
    async def health():
        start = time.time()
        ds_ok = await _check_deepseek()
        qq_ok = await _check_qq()
        return {
            "status": "ok" if (ds_ok and qq_ok) else "degraded",
            "modules": module_status(),
            "services": {"deepseek": ds_ok, "qq": qq_ok},
            "response_ms": round((time.time()-start)*1000),
            "resources": resource_usage(),
        }
    logger.info("健康检查接口已注册: /health")
