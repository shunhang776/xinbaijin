"""
白槿 v2 — 轻量指标采集器。请求量/延迟/错误率/Token/检索命中率/缓存命中率/进程内存。
"""

from __future__ import annotations

import threading

import psutil

__all__ = ["MetricsCollector"]


class MetricsCollector:
    """轻量指标采集，线程安全。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._request_total = 0
        self._request_error = 0
        self._latency_sum_ms = 0.0
        self._latency_max_ms = 0.0
        self._cache_hits = 0
        self._cache_total = 0
        self._search_hits = 0
        self._search_total = 0
        self._token_used: dict[str, int] = {}
        self._process: psutil.Process | None = None

    # ── 记录 ──

    def record_request(self, latency_ms: float, status: str = "ok") -> None:
        with self._lock:
            self._request_total += 1
            self._latency_sum_ms += latency_ms
            if latency_ms > self._latency_max_ms:
                self._latency_max_ms = latency_ms
            if status != "ok":
                self._request_error += 1

    def record_cache(self, hit: bool = True) -> None:
        with self._lock:
            self._cache_total += 1
            if hit:
                self._cache_hits += 1

    def record_search(self, hit: bool = True) -> None:
        with self._lock:
            self._search_total += 1
            if hit:
                self._search_hits += 1

    def record_token(self, count: int, model: str = "default") -> None:
        with self._lock:
            self._token_used[model] = self._token_used.get(model, 0) + count

    def record_error(self, error_type: str = "") -> None:
        """记录一次异常。内部同步递增请求总数，避免错误数 > 总请求数。"""
        with self._lock:
            self._request_total += 1
            self._request_error += 1

    # ── 快照 ──

    def snapshot(self) -> dict:
        with self._lock:
            total = self._request_total
            errors = self._request_error
            avg_latency = (
                self._latency_sum_ms / total if total > 0 else 0.0
            )
            max_latency = self._latency_max_ms
            cache_hit_rate = (
                self._cache_hits / self._cache_total
                if self._cache_total > 0
                else 0.0
            )
            search_hit_rate = (
                self._search_hits / self._search_total
                if self._search_total > 0
                else 0.0
            )
            tokens = dict(self._token_used)

        # 内存采集不持锁，psutil 系统调用不阻塞其他线程
        return {
            "request_total": total,
            "request_error": errors,
            "latency_avg_ms": round(avg_latency, 2),
            "latency_max_ms": round(max_latency, 2),
            "cache_hit_rate": round(cache_hit_rate, 4),
            "search_hit_rate": round(search_hit_rate, 4),
            "token_used": tokens,
            "memory_mb": self._memory_usage_mb(),
        }

    def reset(self) -> None:
        with self._lock:
            self._request_total = 0
            self._request_error = 0
            self._latency_sum_ms = 0.0
            self._latency_max_ms = 0.0
            self._cache_hits = 0
            self._cache_total = 0
            self._search_hits = 0
            self._search_total = 0
            self._token_used.clear()

    # ── 内存 ──

    def _memory_usage_mb(self) -> float:
        try:
            if self._process is None:
                self._process = psutil.Process()
            return round(self._process.memory_info().rss / (1024 * 1024), 2)
        except Exception:
            return 0.0


metrics = MetricsCollector()
