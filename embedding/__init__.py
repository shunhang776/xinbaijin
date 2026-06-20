"""
嵌入模型包（BGE-M3 1024维）。检索/写入独立锁，线程安全。

用法：
    from embedding import preload, is_ready
    preload()    # 启动时预热，加载 BGE-M3 到内存
    is_ready()   # 健康检查用
"""

import logging

logger = logging.getLogger("baijin.embedding")


def preload():
    """启动时预热：加载 BGE-M3 模型到内存。幂等，失败不抛。"""
    import time as _time
    try:
        _t0 = _time.time()
        from .embedder import get_embedder
        print(f"  [preload] import embedder: {_time.time()-_t0:.1f}s", flush=True)

        _t1 = _time.time()
        embedder = get_embedder()
        print(f"  [preload] get_embedder(): {_time.time()-_t1:.1f}s", flush=True)

        # 跑一次空编码验证模型可用
        _t2 = _time.time()
        embedder.encode_query(["ping"])
        print(f"  [preload] encode_query warmup: {_time.time()-_t2:.1f}s", flush=True)

        logger.info("BGE-M3 模型预热完成")
        return True
    except Exception as e:
        logger.warning("BGE-M3 预热失败: %s", e)
        return False


def is_ready() -> bool:
    """健康检查：嵌入模型是否可用。"""
    try:
        from .embedder import _embedder
        if _embedder is None:
            return False
        # 快速验证模型对象可用
        return hasattr(_embedder, "encode_query") and hasattr(_embedder, "encode_doc")
    except Exception:
        return False
