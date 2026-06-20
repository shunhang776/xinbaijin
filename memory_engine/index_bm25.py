"""
BM25 全文索引。2-gram + 3-gram 中文分词，倒排索引全量常驻内存。
"""
import re
import threading
from collections import defaultdict
from math import log
from .config import BM25_K1, BM25_B, TOP_K


class BM25Index:
    """轻量 BM25。全量内存，≤ 5ms。"""

    def __init__(self, k1: float = BM25_K1, b: float = BM25_B):
        self.k1 = k1
        self.b = b
        self._lock = threading.RLock()
        self._docs: dict[str, list[str]] = {}
        self._doc_len: dict[str, int] = {}
        self._inverted: dict[str, set[str]] = defaultdict(set)
        self._avg_dl = 0.0
        self._N = 0

    def add(self, item_id: str, text: str):
        tokens = self._tokenize(text)
        with self._lock:
            self._remove_inner(item_id)
            self._docs[item_id] = tokens
            self._doc_len[item_id] = len(tokens)
            for t in set(tokens):
                self._inverted[t].add(item_id)
            self._N = len(self._docs)
            self._avg_dl = sum(self._doc_len.values()) / self._N

    def remove(self, item_id: str):
        with self._lock:
            self._remove_inner(item_id)
            self._N = len(self._docs)
            self._avg_dl = (sum(self._doc_len.values()) / self._N
                            if self._N > 0 else 0.0)

    def _remove_inner(self, item_id: str):
        if item_id not in self._docs:
            return
        for t in set(self._docs[item_id]):
            self._inverted[t].discard(item_id)
        del self._docs[item_id]
        del self._doc_len[item_id]

    def search(self, query: str, top_k: int = TOP_K) -> list[tuple[str, float]]:
        tokens = self._tokenize(query)
        if not tokens:
            return []
        with self._lock:
            return self._score(tokens, top_k)

    def _score(self, tokens: list[str], top_k: int) -> list[tuple[str, float]]:
        scores: dict[str, float] = defaultdict(float)
        for t in set(tokens):
            df = len(self._inverted.get(t, set()))
            if df == 0:
                continue
            idf = log((self._N - df + 0.5) / (df + 0.5) + 1)
            for doc_id in self._inverted[t]:
                tf = self._docs[doc_id].count(t)
                dl = self._doc_len[doc_id]
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (
                    1 - self.b + self.b * dl / self._avg_dl
                )
                scores[doc_id] += idf * numerator / denominator
        return sorted(scores.items(), key=lambda x: -x[1])[:top_k]

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        text = re.sub(r"[^一-鿿]", "", text)
        tokens = []
        for i in range(len(text)):
            if i + 2 <= len(text):
                tokens.append(text[i:i + 2])
            if i + 3 <= len(text):
                tokens.append(text[i:i + 3])
        return tokens

    def __len__(self) -> int:
        return self._N
