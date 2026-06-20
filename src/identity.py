"""
白槿 AI Native 身份模块。
只提供一段极简身份文本，不包含任何规则、数值、标签。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码。
"""

from pathlib import Path
import threading

_ROOT = Path(__file__).parent.parent
_IDENTITY_PATH = _ROOT / "config" / "identity.txt"
_IDENTITY = ""
_load_lock = threading.RLock()
_loaded = False


def load() -> bool:
    """加载身份文件（线程安全）。成功返回 True。"""
    global _IDENTITY, _loaded
    if _loaded:
        return True
    with _load_lock:
        if _loaded:
            return True
        try:
            if _IDENTITY_PATH.exists():
                with open(_IDENTITY_PATH, encoding="utf-8") as f:
                    _IDENTITY = f.read().strip()
            if not _IDENTITY:
                _IDENTITY = "你是白槿，顺航的AI伴侣。"
            _loaded = True
            return True
        except Exception:
            _IDENTITY = "你是白槿，顺航的AI伴侣。"
            _loaded = True
            return False


def get_identity() -> str:
    return _IDENTITY


def reload() -> bool:
    """强制重新加载身份文件。模型自我优化后调用。"""
    global _loaded
    with _load_lock:
        _loaded = False
    return load()


load()
