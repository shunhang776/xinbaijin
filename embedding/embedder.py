"""
BGE-M3 嵌入模型加载器。检索/写入独立锁，线程安全。
"""
import threading
import numpy as np
from .config_embed import MODEL_NAME, QUERY_INSTRUCTION, USE_FP16

_embedder = None
_lock = threading.RLock()


class BGEEmbedder:
    """BGE-M3 封装。Query/Doc 分锁，检索写入互不阻塞。"""

    def __init__(self):
        from FlagEmbedding import FlagModel
        self.model = FlagModel(
            MODEL_NAME,
            query_instruction_for_retrieval=QUERY_INSTRUCTION,
            use_fp16=USE_FP16,
        )
        self._query_lock = threading.RLock()
        self._doc_lock = threading.RLock()

    def encode_query(self, queries: list[str]) -> np.ndarray:
        with self._query_lock:
            vec = self.model.encode_queries(queries)
            return np.ascontiguousarray(vec)

    def encode_doc(self, docs: list[str]) -> np.ndarray:
        with self._doc_lock:
            vec = self.model.encode(docs)
            return np.ascontiguousarray(vec)


def get_embedder() -> BGEEmbedder:
    global _embedder
    if _embedder is None:
        with _lock:
            if _embedder is None:
                _embedder = BGEEmbedder()
    return _embedder
