"""
白槿 v2 — TTL+LRU 缓存，三防（穿透/雪崩/击穿）。OrderedDict 实现 O(1) LRU。
"""

from __future__ import annotations

import random
import threading
import time
from collections import OrderedDict
from typing import Callable

from core.constants import CACHE_DEFAULT_TTL, CACHE_MAX_SIZE, CACHE_NULL_TTL

__all__ = ["TTLLRUCache"]


class TTLLRUCache:
    """TTL + LRU 混合缓存。线程安全。"""

    def __init__(self, max_size: int = CACHE_MAX_SIZE, default_ttl: int = CACHE_DEFAULT_TTL) -> None:
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._lock = threading.RLock()
        self._store: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._key_locks: dict[str, threading.RLock] = {}

    # ── 内部查询 ──

    def _get_entry(self, key: str) -> tuple[bool, object]:
        """返回 (是否命中, 值)。命中返回 True+值（含 None）；未命中返回 False+None。"""
        with self._lock:
            if key not in self._store:
                self._misses += 1
                return False, None
            expire_at, value = self._store[key]
            if expire_at < time.monotonic():
                self._store.pop(key, None)
                self._misses += 1
                return False, None
            self._store.move_to_end(key)
            self._hits += 1
            return True, value

    # ── 公开接口 ──

    def get(self, key: str) -> object | None:
        _, value = self._get_entry(key)
        return value

    def get_or_compute(self, key: str, factory: Callable[[], object], ttl: int | None = None) -> object:
        exists, result = self._get_entry(key)
        if exists:
            return result

        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.RLock())

        try:
            with key_lock:
                exists, result = self._get_entry(key)
                if exists:
                    return result
                value = factory()
                self.set(key, value, ttl=ttl)
        finally:
            # 释放 key_lock 后清理，身份校验防误删新线程创建的锁
            with self._lock:
                if self._key_locks.get(key) is key_lock:
                    del self._key_locks[key]

        return value

    def set(self, key: str, value: object, ttl: int | None = None) -> None:
        with self._lock:
            base = ttl if ttl is not None else (CACHE_NULL_TTL if value is None else self._default_ttl)
            ttl = int(base * random.uniform(0.9, 1.1))
            expire_at = time.monotonic() + ttl
            self._store[key] = (expire_at, value)
            self._store.move_to_end(key)
            self._evict()

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._store),
                "max_size": self._max_size,
                "hit_rate": self._hits / total if total > 0 else 0.0,
            }

    # ── 内部 ──

    def _evict(self) -> None:
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)


global_cache = TTLLRUCache()
