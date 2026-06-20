"""
白槿自研记忆引擎 — 五层全栈：插件层 → 类别层 → 记忆单元层 → 时间索引层 → 资源层。

获取引擎实例：
    from memory_engine import get_engine
    engine = get_engine()  # 懒加载单例，自动 start()

关闭引擎：
    from memory_engine import shutdown_engine
    shutdown_engine()  # FastAPI shutdown / CLI 退出时调用
"""
from typing import Optional
from .engine import MemoryEngine

# 全局单例（线程安全，延迟初始化）
_engine: Optional[MemoryEngine] = None
_init_lock = None  # 延迟导入 threading，避免 CLI 模式不必要的开销


def get_engine() -> MemoryEngine:
    """全局唯一的 MemoryEngine 单例入口。首次调用自动创建并 start()。"""
    global _engine, _init_lock

    if _engine is not None:
        return _engine

    # 延迟初始化锁，仅在第一次调用时创建
    if _init_lock is None:
        import threading
        _init_lock = threading.RLock()

    with _init_lock:
        # 双重检查锁，防止多线程竞争
        if _engine is None:
            _engine = MemoryEngine()
            _engine.start()

    return _engine


def shutdown_engine() -> None:
    """全局关闭引擎，用于 FastAPI 生命周期和 CLI 退出清理。"""
    global _engine
    if _engine is not None:
        try:
            _engine.shutdown()
        except Exception:
            pass
        _engine = None
