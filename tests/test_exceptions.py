"""
exceptions.py 全面测试：异常体系/链式上下文/字符串表示。
"""
from core.exceptions import (
    BaijinBaseError, InfraError, MemoryError, PluginError, AppError,
)


class TestHierarchy:
    """异常层级测试"""

    def test_all_inherit_from_base(self):
        assert issubclass(InfraError, BaijinBaseError)
        assert issubclass(MemoryError, BaijinBaseError)
        assert issubclass(PluginError, BaijinBaseError)
        assert issubclass(AppError, BaijinBaseError)

    def test_all_inherit_from_exception(self):
        assert issubclass(BaijinBaseError, Exception)


class TestInit:
    """构造测试"""

    def test_message_only(self):
        e = BaijinBaseError("something wrong")
        assert e.message == "something wrong"
        assert e.error_code == ""
        assert e.context == {}

    def test_with_error_code(self):
        e = BaijinBaseError("timeout", error_code="INF_TIMEOUT")
        assert e.message == "timeout"
        assert e.error_code == "INF_TIMEOUT"

    def test_with_context(self):
        e = BaijinBaseError("error", context={"key": "value"})
        assert e.context == {"key": "value"}

    def test_context_defaults_to_empty_dict(self):
        e = BaijinBaseError("msg")
        assert e.context == {}
        # 确保是独立 dict
        e2 = BaijinBaseError("msg")
        assert e2.context is not e.context


class TestStrRepr:
    """字符串表示测试"""

    def test_str_with_code(self):
        e = BaijinBaseError("not found", error_code="MEM_NOT_FOUND")
        assert str(e) == "[MEM_NOT_FOUND] not found"

    def test_str_without_code(self):
        e = BaijinBaseError("just a message")
        assert str(e) == "just a message"

    def test_repr(self):
        e = BaijinBaseError("test", error_code="APP_ERROR")
        r = repr(e)
        assert "BaijinBaseError" in r
        assert "APP_ERROR" in r
        assert "test" in r


class TestWithContext:
    """with_context 链式调用"""

    def test_single_context(self):
        e = BaijinBaseError("err").with_context(user_id="123")
        assert e.context == {"user_id": "123"}

    def test_chained_context(self):
        e = (BaijinBaseError("err")
             .with_context(user_id="123")
             .with_context(trace_id="abc"))
        assert e.context == {"user_id": "123", "trace_id": "abc"}

    def test_returns_self(self):
        e = BaijinBaseError("err")
        assert e.with_context(k="v") is e


class TestLayerErrors:
    """各层异常独立可用"""

    def test_infra_error(self):
        e = InfraError("db down", error_code="INF_DB_ERROR")
        assert isinstance(e, BaijinBaseError)
        assert "INF_DB_ERROR" in str(e)

    def test_memory_error(self):
        e = MemoryError("index fail", error_code="MEM_INDEX_ERROR")
        assert isinstance(e, BaijinBaseError)

    def test_plugin_error(self):
        e = PluginError("exec fail", error_code="PLG_EXEC_ERROR")
        assert isinstance(e, BaijinBaseError)

    def test_app_error(self):
        e = AppError("not found", error_code="APP_NOT_FOUND")
        assert isinstance(e, BaijinBaseError)
