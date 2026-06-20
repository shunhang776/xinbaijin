"""
记忆背景化 — 每次回复自动浸润相关记忆的气息。

不等用户问"我昨天干了什么"，每次对话都触发检索。
检索结果不贴原文，凝为松散语义标签，以重复稀释法注入 prompt 头部。
模型不觉得在"回忆"，只觉得"懂我"。

核心流程：
  检索 top-12 → 计算激活值 → 链式联想 → 竞争归一化 → 相互抑制 → 语义云
"""

import math
import time
import logging
from collections import OrderedDict

logger = logging.getLogger("memory.background")

# ── 可调参数 ──
FORGET_K_NORMAL = 0.1       # 普通记忆遗忘系数
FORGET_K_EMOTIONAL = 0.01   # 高情感记忆遗忘系数
EMOTION_THRESHOLD = 1.3     # 情感强度阈值（超过此值用慢衰减）
RECALL_LOG_FACTOR = 0.2     # 回忆次数的对数压缩系数
SOFTMAX_TEMPERATURE = 0.5   # 竞争归一化温度（<1 强者越强）
INHIBITION_THRESHOLD = 0.05 # 相互抑制：激活值低于此值直接丢弃
BOOST_FACTOR = 0.2          # 共现图传播的加成系数
MAX_CLOUD_TOKENS = 15       # 语义云总 token 上限
COLD_START_MIN = 20         # 冷启动：记忆数 < 此值不启用
COLD_START_TOP3 = 50        # 记忆数 < 此值只取 top-3
CANDIDATE_TOP_K = 12        # 初筛候选数
FINAL_TOP_K = 5             # 最终进入语义云的最大记忆数

# 情绪类型 → 强度系数（中性=1.0，越强越难忘）
EMOTION_MULTIPLIER = {
    "proud": 2.0, "angry": 1.5, "sad": 1.5, "anxious": 1.3,
    "tender": 1.2, "longing": 1.2, "happy": 1.1, "confused": 1.0,
    "neutral": 1.0,
}


def _clean_labels(raw: list[str]) -> list[str]:
    """清洗标签：去标点、空白、空字符串。"""
    result = []
    for label in raw:
        label = label.strip().strip(".,!?;:()[]{}'\"，。！？；：（）【】「」\"")
        if label:
            result.append(label)
    return result


class MemoryBackgrounder:
    """记忆背景化器。每次对话生成潜意识语义云。"""

    def __init__(self, engine):
        self._engine = engine  # MemoryEngine 实例
        self._cache: OrderedDict = OrderedDict()  # 简单 LRU
        self._cache_max = 32

    # ── 主入口 ──

    def get_semantic_cloud(self, user_msg: str,
                           recent_context: str = "",
                           total_memories: int = 0,
                           query_vec=None) -> tuple[str, list[dict], "np.ndarray | None"]:
        """返回 (cloud, candidates, query_vec)。query_vec 供外部复用。"""
        if total_memories < COLD_START_MIN:
            return "", [], None

        # 1. 检索候选记忆
        candidates, q_vec = self._fetch_candidates(user_msg, recent_context, query_vec=query_vec)
        if not candidates:
            return "", [], q_vec

        # 2. 计算原始激活值（归一化前的值保留用于重复稀释）
        raw = self._calc_raw_activations(candidates)
        if not raw:
            return "", candidates, q_vec

        # 3. 链式联想（共现图传播）
        graph = getattr(self._engine, "cooccurrence_graph", None)
        if graph is not None:
            raw = graph.propagate_activation(raw, BOOST_FACTOR)

        # 4. 竞争归一化（用于排序和筛选）
        normalized = self._competitive_normalize(raw)

        # 5. 相互抑制
        normalized = {k: v for k, v in normalized.items()
                      if v >= INHIBITION_THRESHOLD}
        if not normalized:
            return "", candidates, q_vec

        # 6. 冷启动限制
        if total_memories < COLD_START_TOP3:
            kept = set(dict(sorted(normalized.items(),
                                  key=lambda x: -x[1])[:3]).keys())
            normalized = {k: v for k, v in normalized.items() if k in kept}

        # 7. 构建语义云（归一化值排序，原始值决定重复次数）
        cloud = self._build_cloud(normalized, raw, candidates)
        return cloud, candidates, q_vec

    # ── 检索 ──

    def _fetch_candidates(self, user_msg: str,
                          recent_context: str = "",
                          query_vec=None
                          ) -> tuple[list[dict], "np.ndarray | None"]:
        """检索 top-K 候选记忆。返回 (candidates, query_vec)。"""
        query = user_msg
        if recent_context:
            query = f"{recent_context[-200:]} {user_msg}"

        try:
            results, q_vec = self._engine.retrieve.search_sync(query, query_vec=query_vec)
            seen: set[str] = set()
            candidates = []
            for item in results:
                iid = item.get("id", "")
                if not iid or iid in seen:
                    continue
                if item.get("type") == "emotion":
                    continue
                seen.add(iid)
                candidates.append(item)
                if len(candidates) >= CANDIDATE_TOP_K:
                    break
            return candidates, q_vec
        except Exception:
            logger.warning("背景化检索失败", exc_info=True)
            return [], None

    # ── 激活值计算 ──

    def _calc_raw_activations(self,
                              candidates: list[dict]) -> dict[str, float]:
        """为每个候选记忆计算原始激活值。

        公式：a = s × exp(-k × hours) × (1 + emotion_weight)
        其中 s = 检索得分，k = 遗忘系数，emotion_weight = 情绪加成。
        回忆次数通过对数压缩作为微调因子。
        """
        now = time.time()
        activations: dict[str, float] = {}

        for item in candidates:
            similarity = item.get("_score", 0.0)
            if similarity <= 0.1:  # 得分过低直接跳过，避免激活不相关记忆
                continue

            ts = item.get("timestamp", now)
            hours = max((now - ts) / 3600, 0)

            # 遗忘系数：高情感记忆用更慢的衰减
            emotion_mult = self._get_emotion_multiplier(item)
            k = FORGET_K_EMOTIONAL if emotion_mult > EMOTION_THRESHOLD else FORGET_K_NORMAL

            # 回忆次数加成（对数压缩）
            recall_count = item.get("access_count", 0)
            recall_bonus = 1 + RECALL_LOG_FACTOR * math.log(1 + recall_count)

            # 合成激活值
            a = similarity * math.exp(-k * hours) * emotion_mult * recall_bonus

            # 截断到 [0, 1]
            a = max(0.0, min(a, 1.0))
            if a > 0:
                activations[item["id"]] = a

        return activations

    @staticmethod
    def _get_emotion_multiplier(item: dict) -> float:
        """从记忆的关联情绪中提取最大的情绪乘数。"""
        emotions = item.get("associated_emotions", [])
        if not emotions:
            return 1.0
        best = 1.0
        for e in emotions:
            if isinstance(e, dict):
                etype = e.get("type", "neutral")
                intensity = e.get("intensity", 3)
                intensity = max(1, intensity) / 3.0  # 归一化到 [0.33, 1.67]，确保 ≥ 1
                mult = EMOTION_MULTIPLIER.get(etype, 1.0) * intensity
                if mult > best:
                    best = mult
        return best

    # ── 竞争归一化 ──

    def _competitive_normalize(self,
                                activations: dict[str, float]
                                ) -> dict[str, float]:
        """Softmax 归一化（温度 0.5）。
        温度 < 1 使得强者更强、弱者更弱，模拟侧抑制。
        """
        if not activations:
            return {}
        ids = list(activations.keys())
        values = [activations[iid] for iid in ids]

        # 数值稳定：减最大值
        max_v = max(values)
        exp_values = [math.exp((v - max_v) / SOFTMAX_TEMPERATURE) for v in values]
        total = sum(exp_values)
        if total == 0:
            return {}

        return {ids[i]: exp_values[i] / total for i in range(len(ids))}

    # ── 语义云构建 ──

    def _build_cloud(self, normalized: dict[str, float],
                     raw: dict[str, float],
                     candidates: list[dict]) -> str:
        """构建重复稀释语义云。

        normalized: 竞争归一化后的值 → 用于排序
        raw: 原始激活值 → 用于决定标签重复次数
        总 token ≤ 15，空格分隔，无语法结构。
        """
        # 按归一化值降序排列
        ranked = sorted(normalized.items(), key=lambda x: -x[1])
        id_to_item = {item["id"]: item for item in candidates}

        tags: list[str] = []
        for iid, _ in ranked[:FINAL_TOP_K]:
            item = id_to_item.get(iid)
            if not item:
                continue
            labels = self._get_labels(item)
            if not labels:
                continue

            # 原始激活值 → 重复次数：≥0.8→3次, ≥0.5→2次, <0.5→1次
            raw_a = raw.get(iid, 0.0)
            repeat = max(1, int(raw_a * 3 + 0.5))

            for label in labels[:3]:  # 每条记忆最多取 3 个标签
                for _ in range(repeat):
                    if len(tags) >= MAX_CLOUD_TOKENS:
                        return " ".join(tags)
                    tags.append(label)

        if not tags:
            return ""

        cloud = " ".join(tags)
        logger.debug("语义云生成：激活值 %s → %d tokens",
                     {k: round(raw.get(k, 0), 3) for k, _ in ranked[:3]},
                     len(tags))
        return cloud

    @staticmethod
    def _get_labels(item: dict) -> list[str]:
        """从记忆条目中提取并清洗语义标签。
        优先取预计算的 labels 字段，回退到 keywords。
        清洗：去标点、空白、空标签。"""
        # 优先：预计算的标签
        labels = item.get("labels")
        if labels:
            if isinstance(labels, str):
                labels = [t.strip() for t in labels.split(",") if t.strip()]
            if isinstance(labels, list):
                return _clean_labels(labels)

        # 回退：关键词
        keywords = item.get("keywords", [])
        if isinstance(keywords, str):
            import json
            try:
                keywords = json.loads(keywords)
            except json.JSONDecodeError:
                keywords = []
        if isinstance(keywords, list):
            return _clean_labels(keywords[:5])

        return []

    # ── 缓存（同一轮对话重复查询时复用）──

    def cached_cloud(self, cache_key: str, user_msg: str,
                     recent_context: str = "",
                     total_memories: int = 0) -> str:
        """带简单 LRU 缓存的语义云获取。只返回云字符串。"""
        if cache_key and cache_key in self._cache:
            return self._cache[cache_key]

        cloud, _, _ = self.get_semantic_cloud(user_msg, recent_context,
                                              total_memories)

        if cache_key:
            self._cache[cache_key] = cloud
            if len(self._cache) > self._cache_max:
                self._cache.popitem(last=False)

        return cloud

    # ── 暴露检索结果供上游复用（避免双重 FAISS 查询）──

    def get_cloud_and_candidates(self, user_msg: str,
                                 recent_context: str = "",
                                 total_memories: int = 0,
                                 query_vec=None
                                 ) -> tuple[str, list[dict], "np.ndarray | None"]:
        """返回 (cloud, candidates, query_vec)。query_vec 供外部复用。"""
        return self.get_semantic_cloud(user_msg, recent_context, total_memories, query_vec=query_vec)
