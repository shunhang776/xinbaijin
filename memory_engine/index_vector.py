"""
FAISS HNSW 向量索引。仅 float32，强制单线程避免 segfault。
"""
import sys
import logging
import threading
from pathlib import Path
import numpy as np

# 确保父目录在 sys.path 中
_parent = str(Path(__file__).resolve().parent.parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

# 强制 FAISS 单线程，避免 OMP 多线程与业务线程冲突导致 segfault
try:
    import faiss
    faiss.omp_set_num_threads(1)
    faiss.omp_set_dynamic(0)
except Exception:
    pass

from .config import (
    VECTOR_DIM, HNSW_M, EF_CONSTRUCTION, EF_SEARCH,
    FAISS_INDEX_PATH, TOP_K, VECTOR_DISTANCE_MAX,
)

logger = logging.getLogger("memory.vector")


class VectorIndex:
    """FAISS HNSW 向量索引。快照→构建→增量。"""

    def __init__(self, dim: int = VECTOR_DIM):
        self.dim = dim
        self._ready = False
        self._id_to_faiss_idx: dict[str, int] = {}
        self._faiss_idx_to_id: dict[int, str] = {}
        self._next_idx = 0
        self.index = None
        self._lock = threading.RLock()

    # ── 启动 ──

    def load_snapshot(self, id_map: dict[str, int]) -> bool:
        """从磁盘快照恢复。id_map 由上层 db 提供。"""
        if not FAISS_INDEX_PATH.exists():
            return False
        import faiss
        self.index = faiss.read_index(str(FAISS_INDEX_PATH))
        self._id_to_faiss_idx = dict(id_map)
        self._faiss_idx_to_id = {v: k for k, v in id_map.items()}
        self._next_idx = len(id_map)
        self._ready = True
        logger.info("FAISS 快照加载完成，%s 条", self.index.ntotal)
        return True

    def build(self, items: list[tuple[str, str]]):
        """首次全量构建。items = [(item_id, text), ...]"""
        import faiss
        self.index = faiss.IndexHNSWFlat(self.dim, HNSW_M)
        self.index.hnsw.efConstruction = EF_CONSTRUCTION
        self.index.hnsw.efSearch = EF_SEARCH
        if items:
            self._add_batch(items)
        self._ready = True
        logger.info("FAISS 索引构建完成，%s 条", self.index.ntotal)

    def save_snapshot(self) -> dict[str, int]:
        """写磁盘快照，返回 id→faiss_idx 映射供 db 层持久化。"""
        if self.index is None:
            return {}
        import faiss
        faiss.write_index(self.index, str(FAISS_INDEX_PATH))
        return dict(self._id_to_faiss_idx)

    # ── 写入 ──

    def add(self, item_id: str, text: str):
        """单条追加。未就绪时跳过（写入队列兜底重试）。"""
        if not self._ready or self.index is None:
            return
        from embedding.embedder import get_embedder
        emb = get_embedder()
        vec = emb.encode_doc([text])[0]
        vec = np.array([vec], dtype=np.float32)
        with self._lock:
            self.index.add(vec)
            self._id_to_faiss_idx[item_id] = self._next_idx
            self._faiss_idx_to_id[self._next_idx] = item_id
            self._next_idx += 1

    def _add_batch(self, items: list[tuple[str, str]]):
        if not items:
            return
        from embedding.embedder import get_embedder
        emb = get_embedder()
        ids, texts = zip(*items)
        vecs = emb.encode_doc(list(texts))
        vecs = np.array(vecs, dtype=np.float32)
        with self._lock:
            self.index.add(vecs)
            for iid in ids:
                self._id_to_faiss_idx[iid] = self._next_idx
                self._faiss_idx_to_id[self._next_idx] = iid
                self._next_idx += 1

    # ── 检索 ──

    def search(self, query: str, top_k: int = TOP_K,
               max_distance: float = VECTOR_DISTANCE_MAX
               ) -> tuple[list[tuple[str, float]], "np.ndarray | None"]:
        """返回 ([(item_id, distance)], query_vec_1d)。索引未就绪时 query_vec 为 None。"""
        if self.index is None or not self._ready:
            return [], None
        from embedding.embedder import get_embedder
        emb = get_embedder()
        q_vec_1d = emb.encode_query([query])[0]              # 一维：对外复用
        q_vec_2d = np.array([q_vec_1d], dtype=np.float32)    # 二维：FAISS 检索
        results = self.search_vec(q_vec_2d, top_k, max_distance)
        return results, q_vec_1d

    def search_vec(self, q_vec: np.ndarray, top_k: int = TOP_K,
                   max_distance: float = VECTOR_DISTANCE_MAX
                   ) -> list[tuple[str, float]]:
        """用预编码向量检索。返回 [(item_id, distance)]。"""
        if self.index is None or not self._ready:
            return []
        q = np.array([q_vec], dtype=np.float32) if q_vec.ndim == 1 else q_vec
        with self._lock:
            distances, indices = self.index.search(q, top_k)
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1 or float(dist) > max_distance:
                continue
            iid = self._faiss_idx_to_id.get(int(idx))
            if iid and iid in self._id_to_faiss_idx:
                results.append((iid, float(dist)))
        return results

    # ── 维护 ──

    def remove(self, item_id: str):
        """FAISS 不支持单条删除，仅从映射中移除，由下次 rebuild 清理。"""
        if item_id in self._id_to_faiss_idx:
            idx = self._id_to_faiss_idx.pop(item_id)
            self._faiss_idx_to_id.pop(idx, None)

    def rebuild(self, items: list[tuple[str, str]]):
        """全量重建：清理已删除条目，重写索引和映射。"""
        import faiss
        self.index = faiss.IndexHNSWFlat(self.dim, HNSW_M)
        self.index.hnsw.efConstruction = EF_CONSTRUCTION
        self.index.hnsw.efSearch = EF_SEARCH
        self._id_to_faiss_idx.clear()
        self._faiss_idx_to_id.clear()
        self._next_idx = 0
        self._add_batch(items)
        logger.info("FAISS 索引重建完成，%s 条", self.index.ntotal)

    def is_ready(self) -> bool:
        return self._ready

    def __len__(self) -> int:
        return self.index.ntotal if self.index else 0
