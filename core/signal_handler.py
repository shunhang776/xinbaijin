"""
白槿 v2 — 全局信号管理。SIGINT/SIGTERM/Ctrl+C → 优雅停止。

约束：必须在主线程调用 install_signal_handlers()（Python signal 模块限制）。
"""

from __future__ import annotations

import signal
import sys
import threading
from typing import Callable

from core.constants import SHUTDOWN_FORCE_TIMEOUT
from core.logging import get_logger

__all__ = [
    "install_signal_handlers",
    "is_shutting_down",
]

_log = get_logger(__name__)

_INSTALLED = False
_STOPPING = threading.Event()
_lock = threading.RLock()


def is_shutting_down() -> bool:
    """查询当前是否处于关闭流程中。线程安全。"""
    return _STOPPING.is_set()


def install_signal_handlers(stop_callback: Callable[[], None]) -> None:
    """注册 SIGINT/SIGTERM 信号处理。幂等，原子化，多次调用安全。"""
    global _INSTALLED
    with _lock:
        if _INSTALLED:
            return

        def _handle_signal(signum, frame):
            if _STOPPING.is_set():
                _log.warning("重复收到停止信号，强制退出")
                sys.exit(1)

            _STOPPING.set()
            _log.info("收到信号 %d，触发优雅停止", signum)

            # 后台执行停止回调，主线程等待超时兜底
            stop_thread = threading.Thread(target=stop_callback, daemon=True)
            stop_thread.start()
            stop_thread.join(timeout=SHUTDOWN_FORCE_TIMEOUT)

            if stop_thread.is_alive():
                _log.warning("优雅停止超时（%.0fs），强制退出", SHUTDOWN_FORCE_TIMEOUT)
                sys.exit(1)

            _log.info("优雅停止完成")
            sys.exit(0)

        try:
            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
        except AttributeError:
            pass  # 旧版 Python 可能不定义 SIGTERM 常量
        except ValueError:
            _log.warning("信号处理器安装失败：必须在主线程中调用 install_signal_handlers")
            return

        _INSTALLED = True

    _log.info("信号处理器已安装 (SIGINT%s)", " + SIGTERM" if hasattr(signal, "SIGTERM") else "")
