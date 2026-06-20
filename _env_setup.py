"""
_env_setup.py — 环境变量预植入层
===================================

**此文件必须是任何入口点（_launcher.py / main.py / cli.py）的第一个导入。**
在 torch、numpy 等原生库被 import 之前设置线程控制环境变量，
防止 OpenMP / BLAS / MKL 线程爆炸（尤其在 Windows 上）。

约定：
- 每个环境变量优先从 ``core.config.settings`` 读取对应属性，
  若属性不存在或 settings 不可导入，则回退到硬编码默认值。
- 模块被 import 时立即执行所有 os.environ 设置，无需调用任何函数。
- 不依赖 core.config 以外的任何项目模块。
"""

import os as _os

# ──────────────────────────────────────────────
# 1. 尝试读取 settings（可选）
# ──────────────────────────────────────────────
_settings = None
try:
    from core.config import settings as _settings
except Exception:
    _settings = None


def _get(key: str, default: str) -> str:
    """从 settings 读取值，不存在则用默认值。"""
    if _settings is not None:
        val = getattr(_settings, key, None)
        if val is not None:
            return str(val)
    return default


# ──────────────────────────────────────────────
# 2. 线程控制（防止 PyTorch / NumPy 线程爆炸）
# ──────────────────────────────────────────────
_os.environ.setdefault("OMP_NUM_THREADS", _get("omp_num_threads", "1"))
_os.environ.setdefault("OPENBLAS_NUM_THREADS", _get("openblas_num_threads", "1"))
_os.environ.setdefault("MKL_NUM_THREADS", _get("mkl_num_threads", "1"))
_os.environ.setdefault("NUMEXPR_NUM_THREADS", _get("numexpr_num_threads", "1"))

# ──────────────────────────────────────────────
# 3. Windows 特定：允许重复加载 OpenMP 库
# ──────────────────────────────────────────────
_os.environ.setdefault("KMP_DUPLICATE_LIB_OK", _get("kmp_duplicate_lib_ok", "TRUE"))

# ──────────────────────────────────────────────
# 4. 设备控制（CPU-only）
# ──────────────────────────────────────────────
_os.environ.setdefault("CUDA_VISIBLE_DEVICES", _get("cuda_visible_devices", ""))

# ──────────────────────────────────────────────
# 5. Protobuf 兼容性
# ──────────────────────────────────────────────
_os.environ.setdefault(
    "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION",
    _get("protocol_buffers_python_implementation", "python"),
)

# ──────────────────────────────────────────────
# 清理模块级临时符号，避免污染 import 方的命名空间
# ──────────────────────────────────────────────
del _os, _get, _settings
