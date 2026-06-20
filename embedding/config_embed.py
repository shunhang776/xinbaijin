"""
嵌入模型配置。BGE-M3，1024 维，CPU 环境。
路径优先级：环境变量 BGE_MODEL_PATH > 自动检测 > 默认路径。
"""
import os
from pathlib import Path

def _resolve_model_path() -> str:
    # 1. 环境变量优先
    env_path = os.getenv("BGE_MODEL_PATH", "")
    if env_path and Path(env_path).exists():
        return env_path
    # 2. 自动检测常见缓存位置
    candidates = [
        Path.home() / ".cache/huggingface/hub/BAAI/bge-m3",
        Path.home() / ".cache/huggingface/hub/models--BAAI--bge-m3/snapshots",
        Path("/app/models/bge-m3"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    # 3. 默认路径（旧硬编码，向后兼容）
    return "C:/Users/l2038/.cache/huggingface/hub/BAAI/bge-m3"

MODEL_NAME = _resolve_model_path()
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
USE_FP16 = False  # CPU 环境必须关闭，PyTorch CPU 对 fp16 支持残缺
