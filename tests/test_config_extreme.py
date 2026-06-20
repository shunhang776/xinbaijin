"""
config.py 极端测试：hot_reload 回调/嵌套字段/边界值/并发热更。
"""
import threading

import pytest

from core.config import Settings, settings


class TestHotReloadCallbacks:
    """热更回调验证"""

    def test_single_change_callback(self):
        called = []

        def cb(key, value):
            called.append((key, value))

        settings.on_change(cb)
        settings.reload(log_level="DEBUG")

        assert any(k == "log_level" and v == "DEBUG" for k, v in called)
        settings.reload(log_level="INFO")  # 恢复

    def test_batch_change_callback(self):
        called_with = []

        def cb(changes):
            called_with.append(dict(changes))

        settings.on_batch_change(cb)
        settings.reload(log_level="DEBUG")

        assert len(called_with) >= 1
        assert called_with[0].get("log_level") == "DEBUG"
        settings.reload(log_level="INFO")

    def test_multiple_single_callbacks(self):
        count = [0]

        def cb1(key, value):
            count[0] += 1

        def cb2(key, value):
            count[0] += 1

        settings.on_change(cb1)
        settings.on_change(cb2)
        settings.reload(log_level="DEBUG")
        settings.reload(log_level="INFO")

        assert count[0] == 4  # 2 callbacks × 2 changes

    def test_callback_exception_does_not_block_others(self):
        """一个回调抛异常，其他回调仍执行"""
        called = []

        def bad_cb(key, value):
            raise RuntimeError("boom")

        def good_cb(key, value):
            called.append(key)

        settings.on_change(bad_cb)
        settings.on_change(good_cb)
        settings.reload(log_level="DEBUG")
        settings.reload(log_level="INFO")

        assert len(called) == 2  # good_cb 被调了两次


class TestNestedHotReload:
    """嵌套字段热更"""

    def test_nested_feature_field(self):
        original = settings.features.shadow_mode
        settings.reload(**{"features.shadow_mode": not original})
        assert settings.features.shadow_mode == (not original)
        settings.reload(**{"features.shadow_mode": original})

    def test_nested_feature_validation(self):
        """不存在的嵌套字段应该被拒绝"""
        with pytest.raises(ValueError, match="禁止"):
            settings.reload(**{"features.nonexistent": True})


class TestConfigValues:
    """配置值边界测试"""

    def test_vector_weight_range(self):
        assert 0.0 <= settings.vector_weight <= 1.0
        assert 0.0 <= settings.bm25_weight <= 1.0

    def test_weight_validation(self):
        with pytest.raises(ValueError):
            settings.reload(vector_weight=1.5)
        with pytest.raises(ValueError):
            settings.reload(vector_weight=-0.1)

    def test_all_paths_are_paths(self):
        from pathlib import Path
        for attr in ["base_dir", "data_dir", "db_path", "logs_dir"]:
            val = getattr(settings, attr)
            assert isinstance(val, Path), f"{attr} is {type(val)}"

    def test_secret_fields_exist(self):
        for field_name in ["deepseek_api_key", "amap_key", "qq_client_secret"]:
            assert hasattr(settings, field_name)

    def test_config_version(self):
        assert settings.config_version == "v2.0"

    def test_features_config(self):
        assert hasattr(settings.features, "shadow_mode")
        assert hasattr(settings.features, "enable_qq")

    def test_emotion_weights_valid(self):
        weights = settings.emotion_type_weights
        assert isinstance(weights, dict)
        assert all(isinstance(v, float) for v in weights.values())
        assert all(v > 0 for v in weights.values())

    def test_emotion_decay_valid(self):
        decays = settings.emotion_type_decay
        assert isinstance(decays, dict)
        assert all(0 < v < 1 for v in decays.values())

    def test_cron_strings_valid(self):
        import re
        cron_fields = [
            "cleanup_expired_cron", "daily_summary_cron", "backup_cron",
            "light_maintenance_cron", "full_maintenance_cron", "monthly_health_cron",
        ]
        for field in cron_fields:
            value = getattr(settings, field)
            parts = value.split()
            assert len(parts) == 5, f"{field}: {value}"


class TestConcurrentReload:
    """并发热更安全"""

    def test_concurrent_reload_no_crash(self):
        errors = []

        def worker():
            for _ in range(20):
                try:
                    settings.reload(log_level="DEBUG")
                    settings.reload(log_level="INFO")
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # 最终恢复
        settings.reload(log_level="INFO")


class TestFreshSettingsInstance:
    """创建新 Settings 实例不崩溃"""

    def test_multiple_instances_independent(self):
        s1 = Settings()
        s2 = Settings()
        # 两个实例不共享 _reload_lock 等运行时属性
        assert s1._reload_lock is not s2._reload_lock
        assert s1._on_change_callbacks is not s2._on_change_callbacks
