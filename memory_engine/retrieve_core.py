"""
四路并行检索引擎核心。保留原有 RetrieveEngine，供 engine.py 和 retrieve_v2 使用。
"""
import asyncio
import bisect
import logging
import threading
import time as _time
from .config import (
    TOP_K, BM25_WEIGHT, VECTOR_WEIGHT, VECTOR_DISTANCE_MAX,
    EMOTION_TIME_WINDOW_DAYS, EMOTION_MAX_PER_FACT,
    EMOTION_CACHE_TTL, TIMESTAMP_FUTURE_TOLERANCE, TIMESTAMP_MIN_VALID,
    EMOTION_DECAY_RATE, EMOTION_DECAY_MIN,
    EMOTION_WEIGHT_BASE, EMOTION_WEIGHT_MAX,
    EMOTION_TYPE_WEIGHTS, EMOTION_TYPE_DECAY,
    EMOTION_SIMILARITY_THRESHOLD, EMOTION_MATCH_BOOST,
)


def classify_query(query: str) -> list[str]:
    """根据查询内容决定四路结果合并的优先级顺序。"""
    time_kw = ["昨天", "上周", "前天", "下午", "晚上", "今天", "刚才",
               "星期", "几点", "什么时候", "大前天"]
    pref_kw = ["喜欢", "讨厌", "知道", "会", "约定", "答应", "生日",
               "住在", "专业", "习惯", "爱", "怕", "想"]

    if any(k in query for k in time_kw):
        return ["time", "category", "semantic", "keyword"]
    if any(k in query for k in pref_kw):
        return ["category", "semantic", "time", "keyword"]
    return ["semantic", "category", "time", "keyword"]


def _safe_result(result, default):
    """过滤 return_exceptions=True 产生的异常对象。"""
    if isinstance(result, Exception):
        return default
    return result


def _safe_call(fn, default, *args, **kwargs):
    """调用 fn，异常返回 default。"""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return default


def _classify_emotion_type(content: str) -> str:
    """从情绪文本推断类型，具体情绪优先，避免被通用词覆盖。"""
    if any(w in content for w in ["成就感", "搞定", "终于", "成功"]):
        return "proud"
    if any(w in content for w in ["心疼"]):
        return "tender"
    if any(w in content for w in ["惊喜", "感动", "温暖", "幸福", "满足"]):
        return "happy"
    if any(w in content for w in ["开心", "高兴", "笑了", "愉快", "美好"]):
        return "happy"
    if any(w in content for w in ["烦躁", "烦死了", "生气", "愤怒", "恼火", "郁闷", "暴躁"]):
        return "angry"
    if any(w in content for w in ["难过", "悲伤", "哭了", "低落", "失落", "伤心", "沮丧"]):
        return "sad"
    if any(w in content for w in ["担心", "焦虑", "害怕", "紧张", "不安", "慌"]):
        return "anxious"
    if any(w in content for w in ["迷茫", "困惑", "不确定"]):
        return "confused"
    if any(w in content for w in ["想念", "期待", "盼望"]):
        return "longing"
    return "neutral"


# ── 轻量级文本相似度（纯内存，零模型）──

_STOPWORDS = {"的", "了", "在", "是", "我", "你", "他", "她", "它", "和", "与",
              "但", "却", "而", "也", "都", "就", "才", "还", "又", "很", "非常",
              "特别", "有点", "那么", "这么", "一个", "这个", "那个", "什么", "怎么"}
_PROPER_NOUNS = {"FAISS", "BGE", "SQLite", "Python", "顺航", "白槿", "MiniLM",
                 "Mem0", "Chroma", "NPU", "XDNA", "BM25"}


def _extract_keywords(text: str) -> set[str]:
    """提取中文关键词，过滤停用词和短词。"""
    import re
    words = re.findall(r'[一-龥]{2,}|[a-zA-Z]{2,}|\d+', text)
    return {w for w in words if w not in _STOPWORDS}


def _text_similarity(a: str, b: str) -> float:
    """Jaccard 相似度，专有名词加权。"""
    ka = _extract_keywords(a)
    kb = _extract_keywords(b)
    if not ka or not kb:
        return 0.0
    intersection = sum(2 if w in _PROPER_NOUNS else 1 for w in ka & kb)
    union = len(ka | kb)
    return intersection / union


class RetrieveEngine:
    """四路全并行检索。总延迟 = 最慢单路 ≤ 30ms。"""

    def __init__(self, db, time_index, vector_index, bm25_index,
                 associative_index, category_store):
        self.db = db
        self.time_index = time_index
        self.vector_index = vector_index
        self.bm25_index = bm25_index
        self.associative_index = associative_index
        self.category_store = category_store
        self._query_lock = threading.RLock()  # FAISS 多线程查询串行化，防 Windows 死锁

    def _merge(self, query, time_ids, bm25_r, vector_r,
               kw_ids, cat_ctx) -> list[dict]:
        """按动态优先级合并去重。"""
        scores: dict[str, float] = {}

        for iid, s in bm25_r:
            scores[iid] = max(scores.get(iid, 0), s * BM25_WEIGHT)

        for iid, dist in vector_r:
            if dist > VECTOR_DISTANCE_MAX:
                continue
            sim = (VECTOR_DISTANCE_MAX - dist) / VECTOR_DISTANCE_MAX
            scores[iid] = max(scores.get(iid, 0), sim * VECTOR_WEIGHT)

        # Session 事实权重最高，BM25 + 向量两路统一生效
        for iid in list(scores.keys()):
            item = self.db.get_item(iid)
            if item and item.get("type") == "session":
                scores[iid] *= 15

        priority = classify_query(query)
        seen: set[str] = set()
        results: list[dict] = []

        for source in priority:
            if source == "time":
                for iid in time_ids:
                    if iid not in seen:
                        item = self.db.get_item(iid)
                        if item:
                            item["_source"] = "time"
                            results.append(item)
                            seen.add(iid)
            elif source == "category":
                if cat_ctx and "category" not in seen:
                    seen.add("category")
                    results.append({
                        "id": "category", "type": "category",
                        "content": cat_ctx, "_source": "category",
                    })
            elif source == "semantic":
                ranked = sorted(scores.items(), key=lambda x: -x[1])[:TOP_K]
                for iid, s in ranked:
                    if iid not in seen and s > 0:
                        item = self.db.get_item(iid)
                        if item:
                            item["_score"] = round(s, 4)
                            item["_source"] = "semantic"
                            results.append(item)
                            seen.add(iid)
            elif source == "keyword":
                for iid in kw_ids:
                    if iid not in seen:
                        item = self.db.get_item(iid)
                        if item:
                            item["_source"] = "keyword"
                            results.append(item)
                            seen.add(iid)

        # 情绪时间线关联
        self._associate_emotions(results)
        # 情绪触发：用户提到情绪词时，同类型记忆加权
        query_etype = _classify_emotion_type(query)
        if query_etype and query_etype != "neutral":
            for item in results:
                emotions = item.get("associated_emotions", [])
                for e in emotions:
                    if isinstance(e, dict) and e.get("type") == query_etype:
                        old = item.get("_score", 0.1)
                        item["_score"] = max(0.0, round(
                            old * EMOTION_MATCH_BOOST, 4))
                        logging.getLogger("baijin.retrieve").debug(
                            "情绪匹配加权: %s %.4f → %.4f (%s)",
                            item.get("id", "")[:12], old, item["_score"], query_etype)
                        break
        # 情绪加权：强度 × 类型 × 时间衰减
        for item in results:
            emotions = item.get("associated_emotions", [])
            if emotions:
                ts = item.get("timestamp", 0)
                max_weight = 1.0
                for e in emotions:
                    if not isinstance(e, dict):
                        continue
                    intensity = e.get("intensity", 3)
                    etype = e.get("type", "neutral")
                    type_weight = EMOTION_TYPE_WEIGHTS.get(etype, 1.0)
                    # 时间衰减：越久越淡，最低保留 DECAY_MIN
                    days = (int(_time.time()) - ts) / 86400 if ts else 0
                    decay_rate = EMOTION_TYPE_DECAY.get(etype, EMOTION_DECAY_RATE)
                    decay = max(EMOTION_DECAY_MIN, 1 - days * decay_rate)
                    weight = (1 + intensity * EMOTION_WEIGHT_BASE) * type_weight * decay
                    if weight > max_weight:
                        max_weight = weight
                item["_score"] = round(
                    min(item.get("_score", 0.1) * max_weight, EMOTION_WEIGHT_MAX), 4)

        # 加权后重排：category 置顶，其余按 _score 降序
        results.sort(key=lambda x: (0 if x.get("type") == "category" else 1,
                                      -x.get("_score", 0)))
        return results

    # ── 情绪时间线 ──

    _emotion_cache: list[dict] | None = None
    _emotion_cache_ts: int = 0

    def _get_emotion_timeline(self) -> list[dict]:
        """最近 N 天情绪，按时间排序，每条附上 [start, end) 区间。配置化缓存。"""
        now = int(_time.time())
        if self._emotion_cache is not None and now - self._emotion_cache_ts < EMOTION_CACHE_TTL:
            return self._emotion_cache

        cutoff = now - EMOTION_TIME_WINDOW_DAYS * 86400
        emotions = self.db.query(
            "SELECT timestamp, content FROM items "
            "WHERE type='emotion' AND deleted=0 AND timestamp > ? "
            "ORDER BY timestamp",
            (cutoff,),
        )
        if not emotions:
            self._emotion_cache = []
            self._emotion_cache_ts = now
            return []

        valid = [e for e in emotions if self._valid_ts(e["timestamp"], now)]
        if not valid:
            self._emotion_cache = []
            self._emotion_cache_ts = now
            return []

        timeline = []
        for i, e in enumerate(valid):
            start = e["timestamp"]
            end = valid[i + 1]["timestamp"] if i + 1 < len(valid) else now
            content = e["content"]
            intensity = 3
            emotion_type = ""
            if content.startswith("[强]"):
                intensity = 5
                content = content[3:].strip()
            elif content.startswith("[中]"):
                intensity = 3
                content = content[3:].strip()
            elif content.startswith("[微]"):
                intensity = 1
                content = content[3:].strip()
            # 从内容推断情绪类型
            emotion_type = _classify_emotion_type(content)
            timeline.append({
                "start": start, "end": end,
                "content": content, "intensity": intensity,
                "type": emotion_type,
            })

        self._emotion_cache = timeline
        self._emotion_cache_ts = now
        return timeline

    def invalidate_emotion_cache(self):
        """情绪写入后主动失效缓存，新情绪立即生效。"""
        self._emotion_cache = None
        self._emotion_cache_ts = 0

    @staticmethod
    def _valid_ts(ts: int, now: int = 0) -> bool:
        """过滤异常时间戳：早于最小有效值或远超当前时间。"""
        if not ts:
            return False
        if ts < TIMESTAMP_MIN_VALID:
            return False
        current = now or int(_time.time())
        if ts > current + TIMESTAMP_FUTURE_TOLERANCE:
            return False
        return True

    def _associate_emotions(self, results: list[dict]) -> None:
        """时间 + 关键词重叠双重过滤，最多关联 N 条情绪。二分查找。"""
        timeline = self._get_emotion_timeline()
        if not timeline:
            return
        starts = [e["start"] for e in timeline]
        for item in results:
            if item.get("type") in ("emotion", "category"):
                continue
            ts = item.get("timestamp", 0)
            if not self._valid_ts(ts):
                continue
            idx = bisect.bisect_right(starts, ts) - 1
            emotions = []
            item_content = item.get("content", "")
            while idx >= 0 and ts < timeline[idx]["end"] and len(emotions) < EMOTION_MAX_PER_FACT:
                e = timeline[idx]
                content = e["content"]
                # 关键词重叠过滤：相似度 > 阈值才关联
                if item_content:
                    sim = _text_similarity(item_content, content)
                    if sim < EMOTION_SIMILARITY_THRESHOLD:
                        idx -= 1
                        continue
                # 事实内容为空时，直接按时间关联，不检查相似度
                emotions.append({
                    "content": content,
                    "intensity": e.get("intensity", 3),
                    "type": e.get("type", "neutral"),
                })
                idx -= 1
            if emotions:
                item["associated_emotions"] = emotions
                item["associated_emotion"] = emotions[0]["content"]

    def search_sync(self, query: str, query_vec=None) -> tuple[list[dict], "np.ndarray | None"]:
        """同步检索入口。query_vec 由主线程预编码传入，子线程不再调 BGE。"""

        if query_vec is None:
            from embedding.embedder import get_embedder
            try:
                emb = get_embedder()
                query_vec = emb.encode_query([query])[0]
            except Exception:
                query_vec = None

        if query_vec is not None:
            import numpy as np
            query_vec = np.asarray(query_vec, dtype=np.float32).flatten()

        with self._query_lock:
            if query_vec is not None:
                vector_r = _safe_call(self.vector_index.search_vec, [], query_vec, 10)
            else:
                raw = _safe_call(self.vector_index.search, ([], None), query, 10)
                vector_r = raw[0] if isinstance(raw, tuple) else raw

            results = self._merge(
                query,
                _safe_call(self.time_index.search, [], query),
                _safe_call(self.bm25_index.search, [], query),
                vector_r,
                _safe_call(self.associative_index.search, [], query),
                _safe_call(self.category_store.read_relevant, "", query),
            )
        return results, query_vec
