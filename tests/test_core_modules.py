"""
constants.py / paths.py / version.py / config.py / logging.py 综合测试。
"""
import logging
import sys
from pathlib import Path

import pytest

from core.constants import (
    APIPath, ErrorCode, MemoryGrade, PROTECTED_GRADES,
    SECONDS_PER_DAY, SECONDS_PER_HOUR,
    MAX_MEMORY_TEXT_LEN, CACHE_DEFAULT_TTL, CACHE_MAX_SIZE, CACHE_NULL_TTL,
    SHUTDOWN_DRAIN_TIMEOUT, SHUTDOWN_FORCE_TIMEOUT,
    RATE_LIMIT_DEFAULT_RPS, RATE_LIMIT_BURST,
    LOG_RETENTION_DAYS, LOG_MAX_BYTES,
    DEAD_LETTER_MAX_RETRIES, DEAD_LETTER_RETRY_INTERVAL,
)
from core.paths import (
    BASE_DIR, DATA_DIR, DB_PATH, FAISS_INDEX_PATH,
    LOGS_DIR, PERSONA_PATH, DEAD_LETTER_DIR, SNAPSHOT_DIR,
)
from core.config import Settings, settings, FeaturesConfig
from core.logging import get_logger, setup_root_logger, setup_file_logging


# ═══════════════════════════════════════════
# constants.py
# ═══════════════════════════════════════════

class TestAPIPath:
    def test_health_paths(self):
        assert APIPath.HEALTH == "/health"
        assert "/health" in APIPath.HEALTH_LIVENESS

    def test_memory_api_prefix(self):
        assert APIPath.MEMORY_API_PREFIX == "/memory/api"
        assert APIPath.MEMORY_SEARCH == "/memory/api/search"
        assert APIPath.MEMORY_WRITE == "/memory/api/write"

    def test_all_paths_are_strings(self):
        for member in APIPath:
            assert isinstance(member.value, str)

    def test_prefix_not_exposed(self):
        """_PREFIX 不在枚举成员中"""
        assert "_PREFIX" not in [m.name for m in APIPath]


class TestErrorCode:
    def test_codes_are_strings(self):
        for member in ErrorCode:
            assert isinstance(member.value, str)

    def test_prefix_categories(self):
        """各层错误码使用正确前缀"""
        for member in ErrorCode:
            name = member.name
            if name.startswith("INF_"):
                pass
            elif name.startswith("MEM_"):
                pass
            elif name.startswith("PLG_"):
                pass
            elif name.startswith("APP_"):
                pass
            else:
                pytest.fail(f"Unknown error code prefix: {name}")


class TestMemoryGrade:
    def test_values(self):
        assert MemoryGrade.S == "S"
        assert MemoryGrade.A == "A"
        assert MemoryGrade.B == "B"
        assert MemoryGrade.C == "C"

    def test_protected_grades(self):
        assert MemoryGrade.S in PROTECTED_GRADES
        assert MemoryGrade.A in PROTECTED_GRADES
        assert MemoryGrade.B not in PROTECTED_GRADES
        assert MemoryGrade.C not in PROTECTED_GRADES


class TestConstants:
    def test_seconds_per_day(self):
        assert SECONDS_PER_DAY == 86400

    def test_cache_constants_positive(self):
        assert CACHE_DEFAULT_TTL > 0
        assert CACHE_MAX_SIZE > 0
        assert CACHE_NULL_TTL > 0

    def test_shutdown_timeouts(self):
        assert SHUTDOWN_DRAIN_TIMEOUT > 0
        assert SHUTDOWN_FORCE_TIMEOUT > SHUTDOWN_DRAIN_TIMEOUT

    def test_rate_limits(self):
        assert RATE_LIMIT_DEFAULT_RPS > 0
        assert RATE_LIMIT_BURST >= RATE_LIMIT_DEFAULT_RPS

    def test_log_constants(self):
        assert LOG_RETENTION_DAYS > 0
        assert LOG_MAX_BYTES > 0

    def test_dead_letter_constants(self):
        assert DEAD_LETTER_MAX_RETRIES > 0
        assert DEAD_LETTER_RETRY_INTERVAL > 0

    def test_max_memory_text_len(self):
        assert MAX_MEMORY_TEXT_LEN > 0


# ═══════════════════════════════════════════
# paths.py
# ═══════════════════════════════════════════

class TestPaths:
    def test_base_dir_exists(self):
        assert BASE_DIR.is_dir()

    def test_data_dir(self):
        assert DATA_DIR == BASE_DIR / "data"

    def test_db_path(self):
        assert DB_PATH == DATA_DIR / "memory.db"

    def test_faiss_index_path(self):
        assert FAISS_INDEX_PATH == DATA_DIR / "faiss.index"

    def test_logs_dir(self):
        assert LOGS_DIR == BASE_DIR / "logs"

    def test_persona_path(self):
        assert PERSONA_PATH == BASE_DIR / "persona.txt"

    def test_dead_letter_dir(self):
        assert DEAD_LETTER_DIR == DATA_DIR / "dead_letter"

    def test_snapshot_dir(self):
        assert SNAPSHOT_DIR == DATA_DIR / "snapshots"

    def test_all_paths_are_absolute(self):
        for attr_name in [
            "BASE_DIR", "DATA_DIR", "DB_PATH", "LOGS_DIR",
        ]:
            path = getattr(__import__("core.paths", fromlist=[attr_name]), attr_name)
            assert path.is_absolute()


# ═══════════════════════════════════════════
# config.py
# ═══════════════════════════════════════════

class TestSettings:
    def test_settings_singleton_exists(self):
        assert isinstance(settings, Settings)

    def test_default_log_level(self):
        assert settings.log_level == "INFO"

    def test_features_config(self):
        assert isinstance(settings.features, FeaturesConfig)

    def test_features_defaults(self):
        assert settings.features.shadow_mode is False
        assert settings.features.sense_enable is True
        assert settings.features.enable_qq is False

    def test_enable_file_logging(self):
        assert settings.enable_file_logging is True

    def test_secret_fields_are_masked(self):
        """验证 SecretStr 字段存在"""
        assert settings.deepseek_api_key is not None

    def test_hot_reload_fields_discovered(self):
        hot = Settings._hot_reload_fields()
        assert "log_level" in hot
        assert "features.shadow_mode" in hot or "shadow_mode" in str(hot)

    def test_hot_reload_updates_value(self):
        settings.reload(log_level="DEBUG")
        assert settings.log_level == "DEBUG"
        settings.reload(log_level="INFO")  # 恢复

    def test_hot_reload_rejects_unknown_field(self):
        with pytest.raises(ValueError, match="禁止"):
            settings.reload(nonexistent_field=123)

    def test_hot_reload_empty_noop(self):
        result = settings.reload()
        assert result == {}

    def test_reload_from_env_no_crash(self):
        """reload_from_env 不应该崩溃"""
        settings.reload_from_env()


# ═══════════════════════════════════════════
# logging.py
# ═══════════════════════════════════════════

class TestLogging:
    def setup_method(self):
        """每个测试前清理 handler"""
        root = logging.getLogger("baijin")
        root.handlers.clear()

    def test_get_logger(self):
        logger = get_logger("test.module")
        assert logger.name == "baijin.test.module"

    def test_setup_root_logger_creates_handlers(self):
        root = logging.getLogger("baijin")
        root.handlers.clear()
        setup_root_logger("DEBUG")
        assert len(root.handlers) >= 1

    def test_setup_root_logger_idempotent(self):
        root = logging.getLogger("baijin")
        root.handlers.clear()
        setup_root_logger("INFO")
        count1 = len(root.handlers)
        setup_root_logger("DEBUG")
        count2 = len(root.handlers)
        assert count2 == count1  # 不重复添加

    def test_setup_root_logger_updates_level(self):
        root = logging.getLogger("baijin")
        root.handlers.clear()
        setup_root_logger("DEBUG")
        assert root.level == logging.DEBUG
        setup_root_logger("WARNING")
        assert root.level == logging.WARNING
        setup_root_logger("INFO")  # 恢复

    def test_setup_root_logger_accepts_int_level(self):
        root = logging.getLogger("baijin")
        root.handlers.clear()
        setup_root_logger(logging.DEBUG)
        assert root.level == logging.DEBUG
        setup_root_logger("INFO")  # 恢复

    def test_setup_file_logging_with_config_on(self):
        """文件日志根据 enable_file_logging 配置自动挂载"""
        root = logging.getLogger("baijin")
        root.handlers.clear()
        settings.enable_file_logging = True
        setup_root_logger("INFO")
        file_handlers = [h for h in root.handlers
                         if "TimedRotating" in type(h).__name__]
        assert len(file_handlers) >= 1

    def test_setup_file_logging_with_config_off(self):
        root = logging.getLogger("baijin")
        root.handlers.clear()
        settings.enable_file_logging = False
        setup_root_logger("INFO")
        file_handlers = [h for h in root.handlers
                         if "TimedRotating" in type(h).__name__]
        assert len(file_handlers) == 0
        settings.enable_file_logging = True  # 恢复

    def test_json_formatter_output(self):
        root = logging.getLogger("baijin")
        root.handlers.clear()
        setup_root_logger("DEBUG")
        logger = get_logger("test.json")
        # 确保日志不抛异常
        logger.info("test message")
        logger.warning("test warning", exc_info=False)
