"""
BGE-M3 嵌入模型加载器。检索/写入独立锁，线程安全单例。
"""
import threading

import numpy as np

from core.config import settings
from core.exceptions import InfraError
from core.logging import get_logger

from .config import MODEL_PATH, USE_FP16

_logger = get_logger(__name__)

_embedder: "BGEEmbedder | None" = None
_lock = threading.RLock()


class BGEEmbedder:
    """BGE-M3 封装。Query/Doc 分锁，检索写入互不阻塞。"""

    def __init__(self):
        try:
            from FlagEmbedding import FlagModel
        except ImportError as e:
            raise InfraError(
                f"FlagEmbedding 未安装，无法加载 BGE-M3 模型: {e}",
                error_code="INF_EMBED_IMPORT",
            ) from e

        _logger.info("加载嵌入模型: %s (dim=%d)", MODEL_PATH, settings.vector_dim)

        try:
            self.model = FlagModel(
                MODEL_PATH,
                query_instruction_for_retrieval=settings.query_instruction,
                use_fp16=USE_FP16,
            )
        except Exception as e:
            raise InfraError(
                f"加载 BGE-M3 模型失败: {e}",
                error_code="INF_EMBED_LOAD",
            ) from e

        self._query_lock = threading.RLock()
        self._doc_lock = threading.RLock()
        self.model_name = settings.embedding_model_name
        self.vector_dim = settings.vector_dim

    def encode_query(self, queries: list[str]) -> np.ndarray:
        """编码查询向量。使用查询锁，与文档编码互不阻塞。"""
        if not queries:
            raise InfraError("查询列表为空", error_code="INF_EMBED_EMPTY_QUERY")
        with self._query_lock:
            vec = self.model.encode_queries(queries)
            return np.ascontiguousarray(vec)

    def encode_doc(self, docs: list[str]) -> np.ndarray:
        """编码文档向量。使用文档锁，与查询编码互不阻塞。"""
        if not docs:
            raise InfraError("文档列表为空", error_code="INF_EMBED_EMPTY_DOC")
        with self._doc_lock:
            vec = self.model.encode(docs)
            return np.ascontiguousarray(vec)


def get_embedder() -> BGEEmbedder:
    """获取 BGEEmbedder 单例。双重检查锁 + RLock。首次加载时预热。"""
    global _embedder
    if _embedder is None:
        with _lock:
            if _embedder is None:
                _embedder = BGEEmbedder()
                _warmup(_embedder)
    return _embedder


def _warmup(embedder: BGEEmbedder) -> None:
    """首次加载后预热：跑一次 dummy query 触发 JIT/模型初始化。"""
    try:
        _logger.info("嵌入模型预热中...")
        _ = embedder.encode_query(["预热"])
        _logger.info("嵌入模型预热完成")
    except Exception as e:
        _logger.warning("嵌入模型预热失败（不影响正常使用）: %s", e)
