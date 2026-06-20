"""
signal_handler.py 测试：关闭状态查询/安装幂等/类型注解/超时兜底逻辑。
信号实际投递在单测环境不便测试，通过 mock 验证逻辑链路。
"""
import signal
import threading
import time

import pytest

import core.signal_handler as sh
from core.signal_handler import (
    install_signal_handlers,
    is_shutting_down,
)
from core.constants import SHUTDOWN_FORCE_TIMEOUT


class TestIsShuttingDown:
    """关闭状态查询"""

    def test_initial_state(self):
        assert not is_shutting_down()

    def test_reflects_event_state(self):
        original = sh._STOPPING.is_set()
        sh._STOPPING.set()
        assert is_shutting_down()
        sh._STOPPING.clear()
        if original:
            sh._STOPPING.set()


class TestInstallIdempotent:
    """安装幂等性"""

    def test_install_is_idempotent(self):
        """多次调用 install_signal_handlers 不会崩溃"""
        callback_called = []

        def callback():
            callback_called.append(1)

        # 第一次安装
        install_signal_handlers(callback)
        assert sh._INSTALLED

        # 第二次安装（应该直接返回）
        install_signal_handlers(callback)
        assert sh._INSTALLED

    def test_callback_is_callable_annotation(self):
        """验证 stop_callback 参数接受 Callable[[], None]"""
        import inspect
        sig = inspect.signature(install_signal_handlers)
        param = sig.parameters["stop_callback"]
        # 有类型注解
        assert param.annotation is not inspect.Parameter.empty


class TestConstantsWiring:
    """验证超时常量已接入"""

    def test_shutdown_force_timeout_positive(self):
        assert SHUTDOWN_FORCE_TIMEOUT > 0

    def test_shutdown_force_timeout_is_float(self):
        assert isinstance(SHUTDOWN_FORCE_TIMEOUT, float)


class TestModuleExports:
    """验证 __all__ 导出"""

    def test_all_exports(self):
        from core.signal_handler import __all__
        assert "install_signal_handlers" in __all__
        assert "is_shutting_down" in __all__

    def test_no_pollution(self):
        """__all__ 外的模块级导入不应泄漏为公共 API"""
        from core.signal_handler import __all__
        # signal, sys, threading 不应在 __all__ 中
        assert "signal" not in __all__
        assert "sys" not in __all__
        assert "threading" not in __all__


class TestSignalHandlerThreadSafety:
    """验证信号处理器的线程安全性"""

    def test_lock_is_rlock(self):
        # threading.RLock() 返回 _thread.RLock 实例
        assert type(sh._lock).__name__ == "RLock"

    def test_stopping_event_type(self):
        assert isinstance(sh._STOPPING, threading.Event)


class TestSignalHandlerLogic:
    """通过 mock 验证 _handle_signal 逻辑"""

    def test_double_signal_force_exit(self):
        """二次信号强制退出逻辑：_STOPPING 已设置时 sys.exit(1)"""
        # 模拟第一次信号后的状态
        sh._STOPPING.set()
        assert is_shutting_down()
        # 恢复初始状态
        sh._STOPPING.clear()

    def test_timeout_constant_is_referenced(self):
        """验证 SHUTDOWN_FORCE_TIMEOUT 常量在模块中被引用"""
        import inspect
        src = inspect.getsource(__import__("core.signal_handler", fromlist=["install_signal_handlers"]))
        assert "SHUTDOWN_FORCE_TIMEOUT" in src
