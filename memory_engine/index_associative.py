"""
关联索引 — 关键词 → 记忆 ID 映射，dict 直查 ≤ 1ms。
"""
import threading
from collections import defaultdict
from .config import TOP_K


class AssociativeIndex:
    """双向映射：关键词 ↔ item_id。全量内存。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._kw_to_ids: dict[str, set[str]] = defaultdict(set)
        self._id_to_kws: dict[str, set[str]] = {}

    def add(self, item_id: str, keywords: list[str]):
        if not keywords:
            return
        with self._lock:
            self.remove(item_id)
            self._id_to_kws[item_id] = set(keywords)
            for kw in keywords:
                self._kw_to_ids[kw].add(item_id)

    def remove(self, item_id: str):
        with self._lock:
            kws = self._id_to_kws.pop(item_id, set())
            for kw in kws:
                self._kw_to_ids[kw].discard(item_id)

    def search(self, query: str, top_k: int = TOP_K) -> list[str]:
        """按关键词包含匹配，返回命中最多的 item_id 列表。"""
        with self._lock:
            hits: dict[str, int] = defaultdict(int)
            for kw, ids in self._kw_to_ids.items():
                if kw in query:
                    for iid in ids:
                        hits[iid] += 1
            ranked = sorted(hits.items(), key=lambda x: -x[1])[:top_k]
            return [iid for iid, _ in ranked]

    def get_keywords(self, item_id: str) -> list[str]:
        with self._lock:
            return list(self._id_to_kws.get(item_id, set()))

    def __len__(self) -> int:
        return len(self._id_to_kws)
