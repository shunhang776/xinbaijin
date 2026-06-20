"""
白槿 v2 — LLM 客户端：三级降级 + 熔断器 + 指数退避 + Token 统计 + trace_id 传播。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码，锁全用 RLock。
"""

from __future__ import annotations

import asyncio
import json
import random
import threading
import time
from typing import Any, Callable

import httpx

from core.config import settings
from core.constants import APIPath
from core.exceptions import InfraError
from core.logging import get_logger, trace_id_var
from core.metrics import metrics

logger = get_logger(__name__)

# ── 硬编码兜底常量（运行时优先读 core.config.settings）──
FALLBACK = "嗯…我暂时说不了话。"

# 超时 / 熔断 / 重试 兜底值（模块级常量，__init__ 中优先从 settings 读取）
DEFAULT_TIMEOUT = 45.0
FALLBACK_TIMEOUT = 30.0
TOOL_TIMEOUT = 30.0  # 兜底默认值，运行时优先从 settings.llm_tool_timeout 读取

CIRCUIT_THRESHOLD = 3
CIRCUIT_COOLDOWN = 30.0

RETRY_MAX = 3
RETRY_BASE = 1.0
RETRY_MAX_SEC = 60.0
RETRY_JITTER = 0.1

# 备用模型 兜底值（__init__ 中优先从 settings 读取）
FALLBACK_MODEL = "qwen-turbo"
FALLBACK_MAX_TOKENS = 256
FALLBACK_TEMPERATURE = 0.7


def _jittered_delay(attempt: int, base: float = RETRY_BASE,
                    max_delay: float = RETRY_MAX_SEC) -> float:
    """指数退避: base * 2^attempt，上限 max_delay，±10% 抖动。（P1-4: 接受自定义参数）"""
    raw = base * (2 ** attempt)
    bounded = min(raw, max_delay)
    return bounded * (1.0 + random.uniform(-RETRY_JITTER, RETRY_JITTER))


class CircuitBreaker:
    """熔断器：连续失败 ≥ 阈值 → 冷却期内直接拒绝，线程安全（RLock）。"""

    def __init__(self, threshold: int = CIRCUIT_THRESHOLD,
                 cooldown_sec: float = CIRCUIT_COOLDOWN):
        self._lock = threading.RLock()
        self._threshold = threshold
        self._cooldown = cooldown_sec
        self._failure_count = 0
        self._last_failure_time: float = 0.0

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    @property
    def last_failure_time(self) -> float:
        with self._lock:
            return self._last_failure_time

    def is_open(self, threshold: int | None = None,
                cooldown_sec: float | None = None) -> bool:
        """True = 熔断中；冷却期满自动半开。支持运行时覆盖阈值/冷却（热更）。"""
        thresh = threshold if threshold is not None else self._threshold
        cool = cooldown_sec if cooldown_sec is not None else self._cooldown
        with self._lock:
            if self._failure_count < thresh:
                return False
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= cool:
                self._failure_count = 0
                self._last_failure_time = 0.0
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._last_failure_time = 0.0

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._last_failure_time = 0.0


class LLMClient:
    """LLM 客户端：同步/异步双接口，三级降级，指数退避，线程安全。"""

    def __init__(self):
        # ISSUE 5: 备用模型参数优先从 settings 读取，回退到模块常量
        self.fallback_model = getattr(settings, 'llm_fallback_model', FALLBACK_MODEL)
        self.fallback_max_tokens = int(
            getattr(settings, 'llm_fallback_max_tokens', FALLBACK_MAX_TOKENS))
        self.fallback_temperature = float(
            getattr(settings, 'llm_fallback_temperature', FALLBACK_TEMPERATURE))

        self._circuit = CircuitBreaker(
            threshold=self.circuit_threshold,
            cooldown_sec=self.circuit_cooldown)
        self._sync_client: httpx.Client | None = None
        self._client_lock = threading.RLock()

    # ── ISSUE 2: 属性实时从 settings 读取，支持热重载无需重启 ──

    @property
    def default_timeout(self) -> float:
        return float(getattr(settings, 'llm_default_timeout', DEFAULT_TIMEOUT))

    @property
    def fallback_timeout(self) -> float:
        return float(getattr(settings, 'llm_fallback_timeout', FALLBACK_TIMEOUT))

    @property
    def tool_timeout(self) -> float:
        return float(getattr(settings, 'llm_tool_timeout', TOOL_TIMEOUT))

    @property
    def circuit_threshold(self) -> int:
        return int(getattr(settings, 'llm_circuit_threshold', CIRCUIT_THRESHOLD))

    @property
    def circuit_cooldown(self) -> float:
        return float(getattr(settings, 'llm_circuit_cooldown', CIRCUIT_COOLDOWN))

    @property
    def retry_max(self) -> int:
        return int(getattr(settings, 'llm_retry_max', RETRY_MAX))

    @property
    def retry_base(self) -> float:
        return float(getattr(settings, 'llm_retry_base', RETRY_BASE))

    @property
    def retry_max_sec(self) -> float:
        return float(getattr(settings, 'llm_retry_max_sec', RETRY_MAX_SEC))

    # ── 内部：客户端复用 ──

    def _get_client(self) -> httpx.Client:
        with self._client_lock:
            if self._sync_client is None or self._sync_client.is_closed:
                self._sync_client = httpx.Client(
                    timeout=self.default_timeout,
                    limits=httpx.Limits(max_keepalive_connections=4),
                )
            return self._sync_client

    # ── 内部：请求头 / 请求体 ──

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        trace_id = trace_id_var.get()
        if trace_id:
            h["x-trace-id"] = trace_id
        return h

    @staticmethod
    def _body(messages: list[dict], model: str,
              max_tokens: int, temperature: float,
              tools: list[dict] | None = None) -> dict:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
        return body

    # ── 内部：Token 统计 ──

    @staticmethod
    def _record_tokens(resp_json: dict, model: str) -> None:
        usage = resp_json.get("usage", {})
        total = usage.get("total_tokens", 0)
        if total > 0:
            metrics.record_token(int(total), model=model)

    @staticmethod
    def _extract_content(resp_json: dict) -> str:
        content = resp_json["choices"][0]["message"].get("content", "")
        return content.strip() if content else FALLBACK

    # ── 内部：同步 HTTP 请求 ──

    def _post(self, url: str, api_key: str, body: dict,
              timeout: float | None = None) -> dict:
        """同步非流式 POST，返回完整 JSON。异常透传。"""
        client = self._get_client()
        kwargs: dict = {"headers": self._headers(api_key), "json": body}
        if timeout is not None:
            kwargs["timeout"] = timeout
        resp = client.post(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    # ── 内部：异步 HTTP 请求 ──

    async def _post_async(self, url: str, api_key: str, body: dict,
                          timeout: float | None = None) -> dict:
        """异步非流式 POST。每次创建新 AsyncClient 避免跨 loop 共享。"""
        kwargs: dict = {"headers": self._headers(api_key), "json": body}
        if timeout is not None:
            kwargs["timeout"] = timeout
        async with httpx.AsyncClient(timeout=timeout or self.default_timeout) as client:
            resp = await client.post(url, **kwargs)
            resp.raise_for_status()
            return resp.json()

    # ── 内部：指数退避封装 ──

    def _retry(self, fn, *args,
               max_retries: int | None = None,
               base_delay: float | None = None,
               max_delay: float | None = None,
               **kwargs):
        """同步指数退避重试。鉴权错误直接抛 InfraError，限流/网络按退避重试。

        P0-2: 接受 **kwargs 并转发给 fn（如上层的 timeout=TOOL_TIMEOUT）。
        P2-6: 默认重试参数优先来自实例属性（settings），兜底模块常量。

        ISSUE 4: 仅重试网络/HTTP 层异常，不捕获 TypeError/ValueError/KeyError/
        AttributeError/NameError 等编程错误，避免凭退避掩盖代码 bug。
        """
        max_retries = max_retries if max_retries is not None else self.retry_max
        base_delay = base_delay if base_delay is not None else self.retry_base
        max_delay = max_delay if max_delay is not None else self.retry_max_sec
        last_error = None
        for attempt in range(max_retries):
            try:
                return fn(*args, **kwargs)
            except httpx.HTTPStatusError as e:
                last_error = e
                code = e.response.status_code
                if code in (401, 403):
                    raise InfraError(f"LLM鉴权失败({code})") from e
                if code == 429:
                    logger.warning("API 限流(429)，第 %d 次重试", attempt + 1)
                else:
                    logger.warning("HTTP %d，第 %d 次重试", code, attempt + 1)
            except (httpx.TimeoutException, httpx.ConnectError,
                    httpx.NetworkError, OSError) as e:
                last_error = e
                logger.warning("网络错误 %s，第 %d 次重试",
                               type(e).__name__, attempt + 1)
            if attempt < max_retries - 1:
                # P1-4: 传递自定义 base / max_delay
                time.sleep(_jittered_delay(attempt, base_delay, max_delay))
        raise InfraError(f"LLM 请求失败(重试 {max_retries} 次): {last_error}") from last_error

    async def _retry_async(self, fn, *args,
                           max_retries: int | None = None,
                           base_delay: float | None = None,
                           max_delay: float | None = None,
                           **kwargs):
        """异步指数退避重试。

        P0-2: 接受 **kwargs 并转发给 fn。
        P2-6: 默认重试参数优先来自实例属性（settings），兜底模块常量。

        ISSUE 4: 仅重试网络/HTTP 层异常，不捕获编程错误（TypeError/ValueError/
        KeyError/AttributeError/NameError），避免凭退避掩盖代码 bug。
        """
        max_retries = max_retries if max_retries is not None else self.retry_max
        base_delay = base_delay if base_delay is not None else self.retry_base
        max_delay = max_delay if max_delay is not None else self.retry_max_sec
        last_error = None
        for attempt in range(max_retries):
            try:
                return await fn(*args, **kwargs)
            except httpx.HTTPStatusError as e:
                last_error = e
                code = e.response.status_code
                if code in (401, 403):
                    raise InfraError(f"LLM鉴权失败({code})") from e
                if code == 429:
                    logger.warning("API 限流(429)，第 %d 次重试", attempt + 1)
                else:
                    logger.warning("HTTP %d，第 %d 次重试", code, attempt + 1)
            except (httpx.TimeoutException, httpx.ConnectError,
                    httpx.NetworkError, OSError) as e:
                last_error = e
                logger.warning("网络错误 %s，第 %d 次重试",
                               type(e).__name__, attempt + 1)
            if attempt < max_retries - 1:
                await asyncio.sleep(_jittered_delay(attempt, base_delay, max_delay))
        raise InfraError(f"LLM 请求失败(重试 {max_retries} 次): {last_error}") from last_error

    # ── 三级降级：一级 — 主模型 ──

    def _call_primary(self, messages: list[dict], model: str,
                      max_tokens: int, temperature: float) -> str:
        api_key = settings.deepseek_api_key.get_secret_value()
        body = self._body(messages, model, max_tokens, temperature)
        resp = self._post(APIPath.LLM_DEEPSEEK_CHAT.value, api_key, body,
                          timeout=self.default_timeout)
        self._record_tokens(resp, model)
        return self._extract_content(resp)

    async def _call_primary_async(self, messages: list[dict], model: str,
                                  max_tokens: int, temperature: float) -> str:
        api_key = settings.deepseek_api_key.get_secret_value()
        body = self._body(messages, model, max_tokens, temperature)
        resp = await self._post_async(APIPath.LLM_DEEPSEEK_CHAT.value, api_key, body,
                                      timeout=self.default_timeout)
        self._record_tokens(resp, model)
        return self._extract_content(resp)

    # ── 三级降级：二级 — 备用模型 ──

    def _call_fallback(self, messages: list[dict], max_tokens: int) -> str:
        api_key = settings.qwen_api_key.get_secret_value()
        capped = min(max_tokens, self.fallback_max_tokens)
        body = self._body(messages, self.fallback_model, capped, self.fallback_temperature)
        resp = self._post(APIPath.LLM_QWEN_CHAT.value, api_key, body,
                          timeout=self.fallback_timeout)
        self._record_tokens(resp, self.fallback_model)
        return self._extract_content(resp)

    async def _call_fallback_async(self, messages: list[dict],
                                   max_tokens: int) -> str:
        api_key = settings.qwen_api_key.get_secret_value()
        capped = min(max_tokens, self.fallback_max_tokens)
        body = self._body(messages, self.fallback_model, capped, self.fallback_temperature)
        resp = await self._post_async(APIPath.LLM_QWEN_CHAT.value, api_key, body,
                                      timeout=self.fallback_timeout)
        self._record_tokens(resp, self.fallback_model)
        return self._extract_content(resp)

    # ── 三级降级：三级 — 兜底常量 ──

    @staticmethod
    def _call_last_resort() -> str:
        logger.warning("所有模型均不可用，返回兜底常量")
        return FALLBACK

    # ═══════════════════════════════════════════════════════
    #  公有接口
    # ═══════════════════════════════════════════════════════

    def generate(self, messages: list[dict],
                 model: str = "deepseek-chat",
                 max_tokens: int = 500,
                 temperature: float = 0.9) -> str:
        """同步生成。三级降级：主模型 → 备用模型 → FALLBACK。

        P1-3: 熔断器仅守卫主模型（DeepSeek）。熔断打开时跳过一级直接尝试备用模型，
        而不是直接返回 FALLBACK。这样备用模型仍然有机会响应。

        ISSUE 1: 备用模型的成功/失败不触碰主模型熔断器。备用模型成功不代表主模型
        恢复健康，备用模型失败也不应延长主模型冷却期。主模型熔断器完全由自身成败自治。
        """
        if not messages:
            return FALLBACK

        # P1-3: 熔断器仅守卫主模型。打开时跳过一级，不直接返回兜底。
        if not self._circuit.is_open(
                threshold=self.circuit_threshold,
                cooldown_sec=self.circuit_cooldown):
            # 一级：主模型
            try:
                result = self._retry(
                    self._call_primary, messages, model, max_tokens, temperature)
                self._circuit.record_success()
                return result
            except InfraError as e:
                logger.warning("主模型失败: %s，降级到备用", str(e))
                self._circuit.record_failure()
        else:
            logger.warning("主模型熔断中，跳过一级直接降级到备用模型")

        # 二级：备用模型。备用结果不影响主模型熔断器状态。
        try:
            result = self._retry(
                self._call_fallback, messages, max_tokens,
                max_retries=2, base_delay=0.5, max_delay=10.0)
            return result
        except InfraError as e:
            logger.error("备用模型也失败: %s，最终兜底", str(e))

        return self._call_last_resort()

    async def generate_async(self, messages: list[dict],
                             model: str = "deepseek-chat",
                             max_tokens: int = 500,
                             temperature: float = 0.9) -> str:
        """异步生成。三级降级：主模型 → 备用模型 → FALLBACK。

        P1-3: 熔断器仅守卫主模型。熔断打开时跳过一级直接尝试备用模型。

        ISSUE 1: 备用模型的成功/失败不触碰主模型熔断器。
        """
        if not messages:
            return FALLBACK

        if not self._circuit.is_open(
                threshold=self.circuit_threshold,
                cooldown_sec=self.circuit_cooldown):
            try:
                result = await self._retry_async(
                    self._call_primary_async, messages, model, max_tokens, temperature)
                self._circuit.record_success()
                return result
            except InfraError as e:
                logger.warning("主模型失败: %s，降级到备用", str(e))
                self._circuit.record_failure()
        else:
            logger.warning("主模型熔断中，跳过一级直接降级到备用模型")

        # 二级：备用模型。备用结果不影响主模型熔断器状态。
        try:
            result = await self._retry_async(
                self._call_fallback_async, messages, max_tokens,
                max_retries=2, base_delay=0.5, max_delay=10.0)
            return result
        except InfraError as e:
            logger.error("备用模型也失败: %s，最终兜底", str(e))

        return self._call_last_resort()

    # ── 工具调用 ──

    def generate_with_tools(self, messages: list[dict],
                            tools: list[dict],
                            executor: Callable[[str, dict], str],
                            model: str = "deepseek-chat",
                            max_turns: int = 5,
                            max_tokens: int = 500,
                            temperature: float = 0.9) -> str:
        """同步工具调用生成。LLM 最多调用工具 max_turns 轮。

        executor: callable(tool_name: str, arguments: dict) -> str

        P1-3: 熔断器仅守卫主模型。熔断打开时直接降级到普通生成（走完整 fallback 链）。
        ISSUE 3: max_tokens / temperature 可配置，默认与 generate() 一致。
        """
        if not messages or not tools:
            return self.generate(messages, model=model)

        # P1-3: 熔断打开时跳过工具调用，降级到普通生成
        if self._circuit.is_open(
                threshold=self.circuit_threshold,
                cooldown_sec=self.circuit_cooldown):
            logger.warning("主模型熔断中，跳过工具调用，降级到普通生成")
            return self.generate(messages, model=model)

        try:
            return self._run_tool_loop(
                messages, tools, executor, model, max_turns,
                max_tokens, temperature)
        except InfraError as e:
            logger.warning("工具调用失败: %s，降级到普通生成", str(e))
            self._circuit.record_failure()
            return self.generate(messages, model=model)

    def _run_tool_loop(self, messages: list[dict], tools: list[dict],
                       executor: Callable[[str, dict], str],
                       model: str, max_turns: int,
                       max_tokens: int, temperature: float) -> str:
        """工具调用循环：发送 → 解析 tool_calls → 执行 → 追加结果 → 继续。"""
        msgs = list(messages)
        api_key = settings.deepseek_api_key.get_secret_value()

        for _ in range(max_turns):
            body = self._body(msgs, model, max_tokens, temperature, tools=tools)
            # P0-2: timeout is forwarded to _post via **kwargs
            resp = self._retry(
                self._post, APIPath.LLM_DEEPSEEK_CHAT.value, api_key, body,
                timeout=self.tool_timeout,
                max_retries=2, base_delay=0.5, max_delay=10.0)
            self._record_tokens(resp, model)

            tcs = self._parse_tool_calls(resp)
            if not tcs:
                return self._extract_content(resp)

            msgs.append({
                "role": "assistant",
                "tool_calls": tcs,
                "content": None,
            })
            for tc in tcs:
                fn = tc.get("function", tc)
                fn_name = fn.get("name", fn.get("function", {}).get("name", ""))
                try:
                    fn_args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    fn_args = {}
                result = executor(fn_name, fn_args)
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })

        # 超过 max_turns，再做最后一次非工具调用
        body = self._body(msgs, model, max_tokens, temperature)
        resp = self._retry(
            self._post, APIPath.LLM_DEEPSEEK_CHAT.value, api_key, body,
            timeout=self.tool_timeout,
            max_retries=2, base_delay=0.5, max_delay=10.0)
        self._record_tokens(resp, model)
        return self._extract_content(resp)

    @staticmethod
    def _parse_tool_calls(resp: dict) -> list[dict]:
        """从 API 响应提取工具调用列表。兼容 DeepSeek/Qwen 多种格式。"""
        msg = resp.get("choices", [{}])[0].get("message", {})
        if not msg:
            return []
        tcs = msg.get("tool_calls", [])
        if not tcs:
            return []
        parsed = []
        for tc in tcs:
            fn = tc.get("function", {})
            parsed.append({
                "id": tc.get("id", ""),
                "type": tc.get("type", "function"),
                "function": {
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", "{}"),
                },
            })
        return parsed


# ── 模块级单例 ──
llm_client = LLMClient()
