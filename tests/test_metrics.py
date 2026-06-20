"""
metrics.py 全面测试：指标采集/快照/重置/并发安全。
"""
import threading

import pytest

from core.metrics import MetricsCollector


class TestRecord:
    """记录测试"""

    def test_record_request(self):
        m = MetricsCollector()
        m.record_request(100.0)
        s = m.snapshot()
        assert s["request_total"] == 1
        assert s["latency_avg_ms"] == 100.0
        assert s["latency_max_ms"] == 100.0

    def test_record_request_multiple(self):
        m = MetricsCollector()
        m.record_request(50.0)
        m.record_request(150.0)
        s = m.snapshot()
        assert s["request_total"] == 2
        assert s["latency_avg_ms"] == 100.0
        assert s["latency_max_ms"] == 150.0

    def test_record_error(self):
        m = MetricsCollector()
        m.record_error()
        s = m.snapshot()
        assert s["request_total"] == 1
        assert s["request_error"] == 1

    def test_record_error_does_not_exceed_total(self):
        m = MetricsCollector()
        m.record_error()
        m.record_error()
        s = m.snapshot()
        assert s["request_error"] == 2
        assert s["request_total"] == 2  # 同步递增
        assert s["request_error"] <= s["request_total"]

    def test_record_request_error_status(self):
        m = MetricsCollector()
        m.record_request(100.0, status="error")
        s = m.snapshot()
        assert s["request_total"] == 1
        assert s["request_error"] == 1

    def test_record_cache_hit(self):
        m = MetricsCollector()
        m.record_cache(hit=True)
        m.record_cache(hit=False)
        s = m.snapshot()
        assert s["cache_hit_rate"] == 0.5

    def test_record_search_hit(self):
        m = MetricsCollector()
        m.record_search(hit=True)
        m.record_search(hit=True)
        m.record_search(hit=False)
        s = m.snapshot()
        assert s["search_hit_rate"] == pytest.approx(2 / 3, abs=0.01)

    def test_record_token(self):
        m = MetricsCollector()
        m.record_token(1000, "deepseek")
        m.record_token(500, "deepseek")
        m.record_token(200, "embedding")
        s = m.snapshot()
        assert s["token_used"]["deepseek"] == 1500
        assert s["token_used"]["embedding"] == 200


class TestSnapshot:
    """快照测试"""

    def test_empty_snapshot(self):
        m = MetricsCollector()
        s = m.snapshot()
        assert s["request_total"] == 0
        assert s["request_error"] == 0
        assert s["latency_avg_ms"] == 0.0
        assert s["cache_hit_rate"] == 0.0
        assert s["search_hit_rate"] == 0.0
        assert s["token_used"] == {}
        assert isinstance(s["memory_mb"], float)

    def test_snapshot_does_not_mutate_internal_state(self):
        m = MetricsCollector()
        m.record_request(100.0)
        s1 = m.snapshot()
        s1["request_total"] = 999  # 修改快照不应影响内部
        s2 = m.snapshot()
        assert s2["request_total"] == 1

    def test_memory_included(self):
        m = MetricsCollector()
        s = m.snapshot()
        assert s["memory_mb"] > 0


class TestReset:
    """重置测试"""

    def test_reset_zeroes_all(self):
        m = MetricsCollector()
        m.record_request(100.0)
        m.record_error()
        m.record_token(100, "test")
        m.reset()
        s = m.snapshot()
        assert s["request_total"] == 0
        assert s["request_error"] == 0
        assert s["latency_avg_ms"] == 0.0
        assert s["token_used"] == {}


class TestConcurrency:
    """并发安全测试"""

    def test_concurrent_record_does_not_crash(self):
        m = MetricsCollector()
        errors = []

        def worker():
            for _ in range(500):
                try:
                    m.record_request(10.0)
                    m.record_cache(hit=True)
                    m.record_token(10, "test")
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        s = m.snapshot()
        assert s["request_total"] == 5000
