"""
稀疏共现图 — 赫布可塑性工程实现。

「一同激活的神经元会连接在一起。」
写入时：新记忆与当前激活的记忆之间建边。
检索时：链式联想——一个记忆被激活后，与之有边的记忆也获得激活加成。
睡眠整合时：修剪弱边（<0.05），强化强边（×1.2）。

边权重自动随时间衰减（日衰减 5%），模拟突触的自然遗忘。
"""

import time
import threading
import logging
import pickle
from collections import defaultdict

logger = logging.getLogger("memory.cooccurrence")


class CooccurrenceGraph:
    """稀疏无向图：记忆 ID → 邻居 ID → 边权重。全量内存，O(k) 读写。"""

    def __init__(self, max_edges: int = 100000):
        self._lock = threading.RLock()
        # edges[mem_a][mem_b] = weight
        self._edges: dict[str, dict[str, float]] = defaultdict(dict)
        # 每条边的最后更新时间，用于衰减计算
        self._updated: dict[tuple, float] = {}
        # 统计
        self._total_edges = 0
        # 最大边数限制，防止内存溢出
        self._max_edges = max_edges

    # ── 建边 ──

    def add_connection(self, mem_a: str, mem_b: str, strength: float = 0.1):
        """两个记忆同时被激活时，加强它们之间的连接。
        strength 默认 0.1，避免单次共现过拟合。"""
        if mem_a == mem_b:
            return
        now = time.time()
        with self._lock:
            old = self._edges[mem_a].get(mem_b, 0.0)
            self._edges[mem_a][mem_b] = min(old + strength, 1.0)
            self._edges[mem_b][mem_a] = min(self._edges[mem_b].get(mem_a, 0.0) + strength, 1.0)
            key = self._key(mem_a, mem_b)
            self._updated[key] = now
            if old == 0.0:
                self._total_edges += 1

    def add_connections_batch(self, new_mem_id: str, active_ids: list[str],
                              strength: float = 0.1):
        """新记忆写入时，与当前所有激活记忆批量建边。"""
        if not active_ids:
            return
        now = time.time()
        with self._lock:
            for aid in active_ids:
                if aid == new_mem_id:
                    continue
                old = self._edges[new_mem_id].get(aid, 0.0)
                self._edges[new_mem_id][aid] = min(old + strength, 1.0)
                self._edges[aid][new_mem_id] = min(self._edges[aid].get(new_mem_id, 0.0) + strength, 1.0)
                key = self._key(new_mem_id, aid)
                self._updated[key] = now
                if old == 0.0:
                    self._total_edges += 1

    # ── 读边（带衰减）──

    def get_strength(self, mem_a: str, mem_b: str) -> float:
        """获取连接强度，自动应用日衰减 5%。"""
        if mem_a == mem_b:
            return 0.0
        with self._lock:
            raw = self._edges[mem_a].get(mem_b, 0.0)
            if raw == 0.0:
                return 0.0
            key = self._key(mem_a, mem_b)
            last_ts = self._updated.get(key, time.time())
            hours = (time.time() - last_ts) / 3600
            decay = 0.95 ** (hours / 24)
            return raw * decay

    def get_neighbors(self, mem_id: str, min_strength: float = 0.05) -> dict[str, float]:
        """获取某记忆的所有有效邻居（边权 ≥ 阈值）。"""
        with self._lock:
            neighbors = self._edges.get(mem_id, {})
            result = {}
            for nid, raw in neighbors.items():
                s = self.get_strength(mem_id, nid)
                if s >= min_strength:
                    result[nid] = s
            return result

    # ── 链式联想激活 ──

    def propagate_activation(self, activations: dict[str, float],
                             boost_factor: float = 0.2) -> dict[str, float]:
        """给定一组记忆及其原始激活值，通过共现图传播激活。

        【注意】当前为单层传播：仅在输入的记忆之间互相激活，
        不会递归激活它们的邻居。这是故意的性能优化——
        每多一层递归，激活的记忆数会指数增长，引入大量噪音。

        每个记忆的激活增量 = Σ(邻居激活值 × 边权重) × boost_factor。
        返回更新后的激活值字典（原地修改 + 返回）。"""
        with self._lock:
            ids = list(activations.keys())
            if len(ids) < 2:
                return activations

            # 预计算所有边的强度，避免重复调用 get_strength
            strengths: dict[tuple, float] = {}
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    s = self.get_strength(ids[i], ids[j])
                    if s > 0:
                        strengths[(i, j)] = s

            # 单次计算双向激活
            for (i, j), s in strengths.items():
                boost_i = activations[ids[j]] * s * boost_factor
                boost_j = activations[ids[i]] * s * boost_factor
                activations[ids[i]] = min(activations[ids[i]] + boost_i, 1.0)
                activations[ids[j]] = min(activations[ids[j]] + boost_j, 1.0)

            logger.debug("激活传播完成：初始%d个记忆，最终激活值：%s",
                         len(ids), {k: round(v, 3) for k, v in activations.items()})
        return activations

    # ── 睡眠整合 ──

    def consolidate(self) -> dict:
        """修剪弱连接（<0.05），强化强连接（×1.2）。
        模拟睡眠中海马体→皮层记忆转移时的突触重塑。
        超过最大边数时自动修剪权重最低的 10%。
        返回统计信息。"""
        with self._lock:
            to_prune: list[tuple[str, str]] = []
            strengthened = 0
            for mem_a, neighbors in list(self._edges.items()):
                for mem_b, raw in list(neighbors.items()):
                    if mem_a >= mem_b:  # 避免重复处理同一条边
                        continue
                    current = self.get_strength(mem_a, mem_b)
                    if current < 0.05:
                        to_prune.append((mem_a, mem_b))
                    elif current > 0.5:
                        new_w = min(current * 1.2, 1.0)
                        # 只有权重明显变化时才更新时间戳，避免重置衰减计时器
                        if abs(new_w - raw) > 0.01:
                            self._edges[mem_a][mem_b] = new_w
                            self._edges[mem_b][mem_a] = new_w
                            key = self._key(mem_a, mem_b)
                            self._updated[key] = time.time()
                            strengthened += 1

            for mem_a, mem_b in to_prune:
                self._edges[mem_a].pop(mem_b, None)
                self._edges[mem_b].pop(mem_a, None)
                key = self._key(mem_a, mem_b)
                self._updated.pop(key, None)
                self._total_edges = max(0, self._total_edges - 1)

            # 清理空条目
            empty = [mid for mid, n in self._edges.items() if not n]
            for mid in empty:
                del self._edges[mid]

            # 超过最大边数时，修剪权重最低的 10%
            if self._total_edges > self._max_edges:
                all_edges = []
                for mem_a, neighbors in self._edges.items():
                    for mem_b, raw in neighbors.items():
                        if mem_a < mem_b:  # 每边只算一次
                            all_edges.append((self.get_strength(mem_a, mem_b), mem_a, mem_b))
                all_edges.sort()
                to_prune_count = int(len(all_edges) * 0.1)
                for _, mem_a, mem_b in all_edges[:to_prune_count]:
                    self._edges[mem_a].pop(mem_b, None)
                    self._edges[mem_b].pop(mem_a, None)
                    self._updated.pop(self._key(mem_a, mem_b), None)
                    self._total_edges -= 1

        logger.info("共现图整合完成：修剪 %d 条，强化 %d 条，剩余 %d 条边",
                     len(to_prune), strengthened, self._total_edges)
        return {
            "pruned": len(to_prune),
            "strengthened": strengthened,
            "total_edges": self._total_edges,
        }

    # ── 持久化 ──

    def save(self, path: str):
        """保存共现图到磁盘（pickle）。"""
        with self._lock:
            data = {
                "edges": dict(self._edges),
                "updated": dict(self._updated),
                "total_edges": self._total_edges,
                "max_edges": self._max_edges,
            }
            with open(path, "wb") as f:
                pickle.dump(data, f)
        logger.debug("共现图已保存到 %s", path)

    @classmethod
    def load(cls, path: str) -> "CooccurrenceGraph":
        """从磁盘加载共现图。文件不存在时返回空实例。"""
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
            graph = cls(max_edges=data.get("max_edges", 100000))
            graph._edges = defaultdict(dict, data["edges"])
            graph._updated = data["updated"]
            graph._total_edges = data["total_edges"]
            logger.info("共现图已从 %s 加载，包含 %d 条边", path, graph._total_edges)
            return graph
        except FileNotFoundError:
            logger.info("共现图文件 %s 不存在，创建新实例", path)
            return cls()

    # ── 辅助 ──

    @staticmethod
    def _key(mem_a: str, mem_b: str) -> tuple[str, str]:
        """生成边的唯一键，确保 (mem_a, mem_b) 和 (mem_b, mem_a) 返回相同结果。"""
        return tuple(sorted((mem_a, mem_b)))

    def remove_node(self, mem_id: str):
        """删除某记忆的所有连接（记忆被永久删除时调用）。"""
        with self._lock:
            neighbors = list(self._edges.get(mem_id, {}).keys())
            for nid in neighbors:
                self._edges[nid].pop(mem_id, None)
                key = self._key(mem_id, nid)
                self._updated.pop(key, None)
                self._total_edges = max(0, self._total_edges - 1)
            self._edges.pop(mem_id, None)
            logger.debug("已删除记忆节点 %s 及其 %d 条连接", mem_id, len(neighbors))

    def __len__(self) -> int:
        return self._total_edges

    def stats(self) -> dict:
        """返回统计信息。"""
        with self._lock:
            nodes = len(self._edges)
            edges = self._total_edges
            avg_degree = edges / nodes if nodes > 0 else 0
            return {
                "nodes": nodes,
                "edges": edges,
                "avg_degree": round(avg_degree, 2),
            }
