"""
cache.py 极端测试：抖动分布/空值过期一致性/TTL 边界/高并发压力。
"""
import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from core.cache import TTLLRUCache


class TestJitterDistribution:
    """验证 TTL 抖动在 0.9~1.1 范围内均匀分布"""

    def test_jitter_within_range(self):
        """100 次 set，TTL 抖动全在 [base*0.9, base*1.1] 内"""
        c = TTLLRUCache(default_ttl=100)
        # 通过 _get_entry 访问内部 expire_at 来反推 TTL
        now = time.monotonic()
        for i in range(100):
            c.set(f"k{i}", i)

        # 检查所有 key 的 expire_at 在合理范围
        with c._lock:
            for key, (expire_at, _) in c._store.items():
                ttl = expire_at - now
                # default_ttl=100 * 0.9 = 90, * 1.1 = 110
                assert 90 <= ttl <= 110, f"TTL {ttl:.1f} out of [90, 110]"

    def test_jitter_varies(self):
        """多次 set 的 TTL 不完全相同（抖动生效）"""
        c = TTLLRUCache(default_ttl=100)
        now = time.monotonic()
        for i in range(20):
            c.set(f"k{i}", i)

        with c._lock:
            ttls = [expire_at - now for expire_at, _ in c._store.values()]

        # 至少有两个不同的 TTL 值（随机抖动生效）
        unique = len(set(round(t, 1) for t in ttls))
        assert unique >= 2, f"All {len(ttls)} TTLs identical, jitter likely not working"

    def test_null_jitter_varies(self):
        """None 值缓存也有抖动"""
        c = TTLLRUCache()
        now = time.monotonic()
        for i in range(20):
            c.set(f"null{i}", None)

        with c._lock:
            ttls = [expire_at - now for expire_at, _ in c._store.values()]

        unique = len(set(round(t, 1) for t in ttls))
        assert unique >= 2, f"Null TTLs all identical, jitter not applied"


class TestTTLBoundaries:
    """TTL 边界值测试"""

    def test_ttl_zero(self):
        """ttl=0 立即过期"""
        c = TTLLRUCache()
        c.set("a", 1, ttl=0)
        time.sleep(0.1)
        assert c.get("a") is None

    def test_ttl_negative(self):
        """ttl=-1 也立即过期"""
        c = TTLLRUCache()
        c.set("a", 1, ttl=-1)
        time.sleep(0.05)
        assert c.get("a") is None

    def test_ttl_very_long(self):
        """ttl 很大，不过期"""
        c = TTLLRUCache()
        c.set("a", 1, ttl=86400)
        assert c.get("a") == 1

    def test_default_ttl_zero_instant_expiry(self):
        """default_ttl=0 构造的缓存立即过期"""
        c = TTLLRUCache(default_ttl=0)
        c.set("a", 1)
        time.sleep(0.1)
        assert c.get("a") is None


class TestExtremeConcurrency:
    """极端并发压力"""

    def test_massive_concurrent_reads(self):
        """100 线程 × 100 次读取，不崩溃"""
        c = TTLLRUCache(max_size=1000)
        for i in range(100):
            c.set(f"k{i}", i)

        errors = []

        def reader():
            try:
                for _ in range(100):
                    key = f"k{random.randint(0, 99)}"
                    c.get(key)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=50) as ex:
            futures = [ex.submit(reader) for _ in range(100)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0

    def test_mixed_operations_stress(self):
        """混合读写删操作，50 线程并发"""
        c = TTLLRUCache(max_size=500)
        stop = threading.Event()
        errors = []

        def worker(seed):
            rng = random.Random(seed)
            try:
                for _ in range(200):
                    op = rng.choice(["get", "set", "delete", "get_or_compute"])
                    key = f"k{rng.randint(0, 50)}"
                    if op == "get":
                        c.get(key)
                    elif op == "set":
                        c.set(key, rng.randint(0, 1000))
                    elif op == "delete":
                        c.delete(key)
                    else:
                        c.get_or_compute(key, lambda: "computed", ttl=1)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=25) as ex:
            futures = [ex.submit(worker, i) for i in range(50)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"errors: {errors}"

    def test_single_key_stampede(self):
        """100 线程同时 get_or_compute 同一 key，factory 只调一次"""
        c = TTLLRUCache()
        counter = 0
        lock = threading.Lock()

        def expensive_factory():
            nonlocal counter
            with lock:
                counter += 1
            time.sleep(0.02)
            return f"result-{counter}"

        results = []

        def worker():
            results.append(c.get_or_compute("hot_key", expensive_factory))

        threads = [threading.Thread(target=worker) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert counter == 1, f"factory called {counter} times (expected 1)"
        assert all(r == "result-1" for r in results)

    def test_lru_stress_eviction(self):
        """大量写入触发 LRU 淘汰，不丢数据不崩溃"""
        c = TTLLRUCache(max_size=10)
        for i in range(1000):
            c.set(f"k{i}", i)
        # 最后 10 个应该在缓存中
        for i in range(990, 1000):
            assert c.get(f"k{i}") == i
        # 前面的大多数应该被淘汰
        assert c.stats()["size"] <= 10


class TestCacheStatsUnderLoad:
    """负载下统计正确性"""

    def test_hit_rate_under_concurrent_load(self):
        c = TTLLRUCache()
        c.set("a", 1)

        def worker():
            for _ in range(50):
                c.get("a")
                c.get("nonexistent")

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        s = c.stats()
        # 一半命中缓存，一半 miss
        assert 0.45 < s["hit_rate"] < 0.55, f"hit_rate={s['hit_rate']}"
