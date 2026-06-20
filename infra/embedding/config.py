"""
嵌入模型配置。从 core.config.settings 读取，跨平台模型路径处理。
"""
import os
from pathlib import Path

from core.config import settings

# ── 不可变常量 ──
USE_FP16: bool = False  # CPU 环境必须关闭，PyTorch CPU 对 fp16 支持残缺


def resolve_model_path() -> str:
    """跨平台解析模型路径。

    优先级：环境变量 BGE_MODEL_PATH > HF_HOME 缓存自动检测 > 用户默认缓存 > 回退路径。
    支持 HuggingFace 新旧两种缓存目录格式。
    """
    # 1. 环境变量优先
    env_path = os.getenv("BGE_MODEL_PATH", "")
    if env_path and Path(env_path).exists():
        return env_path

    model_name = settings.embedding_model_name  # e.g. "BAAI/bge-m3"
    org, name = (model_name.split("/", 1) + [""])[:2]

    # 2. 自动检测 HuggingFace 缓存
    hf_home = os.getenv("HF_HOME", str(Path.home() / ".cache" / "huggingface"))

    # 新格式：~/.cache/huggingface/hub/models--{org}--{name}/snapshots/<hash>
    snapshots_dir = Path(hf_home) / "hub" / f"models--{org}--{name}" / "snapshots"
    if snapshots_dir.is_dir():
        snapshots = sorted(
            [d for d in snapshots_dir.iterdir() if d.is_dir()],
            reverse=True,
        )
        if snapshots:
            return str(snapshots[0])

    # 旧格式：~/.cache/huggingface/hub/{org}/{name}
    legacy_dir = Path(hf_home) / "hub" / model_name
    if legacy_dir.exists():
        return str(legacy_dir)

    # 3. 默认回退路径（向后兼容）
    return str(Path.home() / ".cache" / "huggingface" / "hub" / model_name)


MODEL_PATH: str = resolve_model_path()
