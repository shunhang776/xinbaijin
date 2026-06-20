"""
logging.py 极端测试：JSON格式/敏感字段脱敏/trace_id注入/文件日志轮转。
"""
import json
import logging
import tempfile
from pathlib import Path

import pytest

from core.logging import (
    get_logger, setup_root_logger, setup_file_logging,
    trace_id_var, span_id_var,
    _SENSITIVE_RE, _JsonFormatter,
)
from core.config import settings


class TestJsonFormatter:
    """JSON 格式化输出验证"""

    def test_format_produces_json(self):
        """_JsonFormatter.format 返回合法 JSON"""
        formatter = _JsonFormatter()
        record = logging.LogRecord(
            "baijin.test", logging.INFO, "test.py", 10, "hello world", [], None
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "baijin.test"
        assert parsed["message"] == "hello world"
        assert "trace_id" in parsed
        assert "timestamp" in parsed

    def test_format_injects_trace_id(self):
        """trace_id 自动注入到 JSON 输出"""
        trace_id_var.set("trace-123")
        span_id_var.set("span-456")

        formatter = _JsonFormatter()
        record = logging.LogRecord(
            "baijin.test", logging.INFO, "test.py", 10, "traced msg", [], None
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["trace_id"] == "trace-123"
        assert parsed["span_id"] == "span-456"

        trace_id_var.set("")
        span_id_var.set("")

    def test_format_includes_exception(self):
        """有异常时包含 exception 字段"""
        formatter = _JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord(
                "baijin.test", logging.ERROR, "test.py", 10, "error msg", [], None
            )
            import sys
            record.exc_info = sys.exc_info()
            output = formatter.format(record)
            parsed = json.loads(output)
            assert "exception" in parsed
            assert "ValueError" in parsed["exception"]

    def test_format_sensitive_masking(self):
        """敏感字段在 JSON 输出中被脱敏"""
        formatter = _JsonFormatter()
        record = logging.LogRecord(
            "baijin.test", logging.INFO, "test.py", 10,
            "api_key=sk-secret123 and normal text", [], None
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "sk-secret123" not in parsed["message"]
        assert "***" in parsed["message"]
        assert "api_key=" in parsed["message"]

    def test_module_and_lineno_in_record(self):
        logger = get_logger("test.module_line")
        # module 和 lineno 由 logging 自动填充
        record = logging.LogRecord(
            "baijin.test.module_line", logging.INFO,
            "test.py", 42, "msg", [], None
        )
        assert record.module == "test"
        assert record.lineno == 42


class TestSensitiveMasking:
    """敏感字段脱敏验证"""

    def test_api_key_masked(self):
        result = _SENSITIVE_RE.sub(r'\1\2***', "api_key=sk-abc123secret")
        assert "sk-abc123secret" not in result
        assert "***" in result
        assert "api_key=" in result  # key名保留

    def test_token_masked(self):
        result = _SENSITIVE_RE.sub(r'\1\2***', "access_token: eyJhbGciOiJIUzI1NiJ9.xxx")
        assert "eyJhbGci" not in result
        assert "***" in result

    def test_password_masked(self):
        result = _SENSITIVE_RE.sub(r'\1\2***', "password=mypassword123")
        assert "mypassword123" not in result
        assert "***" in result

    def test_normal_text_unaffected(self):
        result = _SENSITIVE_RE.sub(r'\1\2***', "user said hello world")
        assert result == "user said hello world"

    def test_case_insensitive(self):
        result = _SENSITIVE_RE.sub(r'\1\2***', "API_KEY=SECRET123")
        assert "SECRET123" not in result
        assert "***" in result

    def test_colon_separator(self):
        result = _SENSITIVE_RE.sub(r'\1\2***', "secret: mysecret")
        assert "mysecret" not in result
        assert "***" in result


class TestFileLogging:
    """文件日志测试"""

    def test_setup_file_logging_creates_dir(self, tmp_path):
        log_dir = tmp_path / "logs"
        # 重置 handler 避免幂等检查
        root = logging.getLogger("baijin")
        root.handlers.clear()

        setup_file_logging(str(log_dir))
        assert log_dir.is_dir()

    def test_file_logging_writes(self, tmp_path):
        log_dir = tmp_path / "logs"
        root = logging.getLogger("baijin")
        root.handlers.clear()
        setup_root_logger("DEBUG")

        # 清除自动挂载的文件 handler，用自定义路径
        for h in list(root.handlers):
            if "TimedRotating" in type(h).__name__:
                root.removeHandler(h)

        setup_file_logging(str(log_dir))
        logger = get_logger("test.file")
        logger.info("file log test")

        # 关闭 handler 以刷新缓冲区
        for h in root.handlers:
            if "TimedRotating" in type(h).__name__:
                h.close()

        # 应该有日志文件
        log_files = list(log_dir.glob("baijin.log*"))
        assert len(log_files) >= 1


class TestGetLogger:
    """get_logger 命名空间测试"""

    def test_hierarchy(self):
        logger = get_logger("core.cache")
        assert logger.name == "baijin.core.cache"

    def test_parent_propagation(self):
        child = get_logger("deep.nested.module")
        assert child.name.startswith("baijin.")

    def test_same_name_same_logger(self):
        a = get_logger("test.same")
        b = get_logger("test.same")
        assert a is b


class TestLogLevelUpdate:
    """日志级别更新测试"""

    def test_multiple_level_changes(self):
        root = logging.getLogger("baijin")
        root.handlers.clear()

        for level in ["DEBUG", "INFO", "WARNING", "ERROR"]:
            setup_root_logger(level)
            assert root.level == getattr(logging, level)

        setup_root_logger("INFO")  # 恢复


class TestContextVars:
    """ContextVar 行为验证"""

    def setup_method(self):
        """每个测试前清空 context vars"""
        trace_id_var.set("")
        span_id_var.set("")

    def test_default_empty(self):
        assert trace_id_var.get() == ""
        assert span_id_var.get() == ""

    def test_set_and_clear(self):
        trace_id_var.set("test-123")
        assert trace_id_var.get() == "test-123"
        trace_id_var.set("")
        assert trace_id_var.get() == ""
