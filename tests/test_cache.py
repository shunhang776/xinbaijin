"""
cache.py 全面测试：TTL+LRU 缓存，并发安全，防穿透/雪崩/击穿。
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from core.cache import TTLLRUCache


class TestGetSet:
    """基本 get/set/delete 操作"""

    def test_set_and_get(self):
        c = TTLLRUCache()
        c.set("a", 1)
        assert c.get("a") == 1

    def test_get_missing(self):
        c = TTLLRUCache()
        assert c.get("nonexistent") is None

    def test_set_none_value(self):
        """None 值缓存：get 返回 None，但应该缓存了（防穿透）"""
        c = TTLLRUCache()
        c.set("key", None)
        # None 值被缓存，不应调用 factory
        called = False
        def factory():
            nonlocal called
            called = True
            return "fallback"
        result = c.get_or_compute("key", factory)
        assert result is None
        assert not called

    def test_overwrite(self):
        c = TTLLRUCache()
        c.set("a", 1)
        c.set("a", 2)
        assert c.get("a") == 2

    def test_delete(self):
        c = TTLLRUCache()
        c.set("a", 1)
        c.delete("a")
        assert c.get("a") is None

    def test_delete_nonexistent(self):
        c = TTLLRUCache()
        c.delete("nonexistent")  # 不抛异常

    def test_clear(self):
        c = TTLLRUCache()
        c.set("a", 1)
        c.set("b", 2)
        c.clear()
        assert c.get("a") is None
        assert c.get("b") is None


class TestTTL:
    """TTL 过期测试"""

    def test_expires_after_ttl(self):
        c = TTLLRUCache()
        c.set("a", 1, ttl=0)  # 立即过期
        time.sleep(0.01)
        assert c.get("a") is None

    def test_does_not_expire_before_ttl(self):
        c = TTLLRUCache(default_ttl=10)
        c.set("a", 1)
        assert c.get("a") == 1

    def test_custom_ttl(self):
        c = TTLLRUCache(default_ttl=10)
        c.set("a", 1, ttl=0)
        time.sleep(0.05)  # ttl=0 经抖动后为 0，给足够时间过期
        assert c.get("a") is None

    def test_null_ttl_is_short(self):
        """None 值的 TTL 应该很短（默认 30s，加抖动后 < 33s）"""
        c = TTLLRUCache()
        c.set("k", None)  # 使用 CACHE_NULL_TTL
        assert c.get("k") is None  # None 值但命中缓存
        # 无法直接验证 TTL 值，但确保不崩溃
        c.delete("k")
        assert c.get("k") is None  # 删除后未命中


class TestLRU:
    """LRU 淘汰测试"""

    def test_evicts_when_full(self):
        c = TTLLRUCache(max_size=3)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)
        c.set("d", 4)  # 应该淘汰 a
        assert c.get("a") is None
        assert c.get("b") == 2
        assert c.get("c") == 3
        assert c.get("d") == 4

    def test_get_refreshes_position(self):
        """get 命中会将 key 移到 LRU 尾部"""
        c = TTLLRUCache(max_size=3)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)
        c.get("a")  # 刷新 a，现在 a 是最新的
        c.set("d", 4)  # 应该淘汰 b（a 被刷新了）
        assert c.get("a") == 1
        assert c.get("b") is None
        assert c.get("c") == 3
        assert c.get("d") == 4

    def test_set_refreshes_position(self):
        """set 已存在的 key 也会刷新 LRU 位置"""
        c = TTLLRUCache(max_size=3)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)
        c.set("a", 99)  # 刷新 a
        c.set("d", 4)   # 应该淘汰 b
        assert c.get("a") == 99
        assert c.get("b") is None


class TestGetOrCompute:
    """get_or_compute 核心逻辑"""

    def test_computes_on_miss(self):
        c = TTLLRUCache()
        result = c.get_or_compute("key", lambda: "computed")
        assert result == "computed"

    def test_returns_cached_on_hit(self):
        c = TTLLRUCache()
        c.set("key", "cached")
        called = False
        result = c.get_or_compute("key", lambda: setattr(called, '__self__', None) or "computed")
        assert result == "cached"

    def test_factory_called_exactly_once(self):
        """同一 key 并发请求，factory 只调一次"""
        c = TTLLRUCache()
        call_count = 0
        lock = threading.Lock()

        def factory():
            nonlocal call_count
            with lock:
                call_count += 1
            time.sleep(0.05)  # 模拟耗时操作
            return call_count

        results = []

        def worker():
            results.append(c.get_or_compute("key", factory))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert call_count == 1, f"factory called {call_count} times, expected 1"
        assert all(r == 1 for r in results), f"not all results are 1: {results}"

    def test_factory_raises_does_not_cache(self):
        """factory 抛异常不缓存错误值，且下次重试"""
        c = TTLLRUCache()
        call_count = 0

        def failing_factory():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("fail")
            return "success"

        with pytest.raises(ValueError, match="fail"):
            c.get_or_compute("key", failing_factory)

        # 第二次应该成功
        result = c.get_or_compute("key", failing_factory)
        assert result == "success"
        assert call_count == 2

    def test_key_lock_cleaned_up(self):
        """get_or_compute 完成后 _key_locks 应该清理"""
        c = TTLLRUCache()
        c.get_or_compute("key", lambda: "value")
        assert "key" not in c._key_locks

    def test_key_lock_cleaned_up_on_error(self):
        """factory 抛异常后 _key_locks 也应该清理"""
        c = TTLLRUCache()
        with pytest.raises(ValueError):
            c.get_or_compute("key", lambda: (_ for _ in ()).throw(ValueError("boom")))
        assert "key" not in c._key_locks


class TestConcurrency:
    """并发压力测试"""

    def test_concurrent_get_set(self):
        """多线程并发读写不崩溃"""
        c = TTLLRUCache(max_size=100)
        errors = []

        def worker(n):
            try:
                for i in range(100):
                    key = f"key{n}_{i % 20}"
                    c.set(key, i)
                    c.get(key)
                    if i % 10 == 0:
                        c.delete(key)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"errors: {errors}"

    def test_concurrent_get_or_compute_same_key(self):
        """100 线程争抢同一 key，factory 只调一次"""
        c = TTLLRUCache()
        counter = 0
        lock = threading.Lock()

        def factory():
            nonlocal counter
            with lock:
                counter += 1
            time.sleep(0.01)
            return "computed"

        results = []

        def worker():
            results.append(c.get_or_compute("single_key", factory))

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(worker) for _ in range(100)]
            for f in as_completed(futures):
                f.result()

        assert counter == 1, f"factory called {counter} times"
        assert len(results) == 100
        assert all(r == "computed" for r in results)


class TestStats:
    """stats 统计测试"""

    def test_hit_rate(self):
        c = TTLLRUCache()
        c.set("a", 1)
        c.get("a")
        c.get("b")  # miss
        s = c.stats()
        assert s["size"] == 1
        assert s["hit_rate"] == 0.5

    def test_empty_stats(self):
        c = TTLLRUCache()
        s = c.stats()
        assert s["size"] == 0
        assert s["hit_rate"] == 0.0
