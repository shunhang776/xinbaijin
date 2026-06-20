"""
主引擎入口。启动序列、关闭序列、统一 search/write 接口。
"""
import re
import logging
import shortuuid
from .db import Database
from .utils import now_ts, validate_ts
from .index_time import TimeIndex
from .index_vector import VectorIndex
from .index_bm25 import BM25Index
from .index_associative import AssociativeIndex
from .category_store import CategoryStore
from .resource_store import ResourceStore
from .retrieve_core import RetrieveEngine
from .write_pipeline import WritePipeline
from .write_pipeline import _keywords_to_labels
from .evolve import EvolutionEngine
from .cooccurrence import CooccurrenceGraph
from .background import MemoryBackgrounder
from .config import COOCCURRENCE_PATH

logger = logging.getLogger("memory.engine")


_engine = None  # 全局单例，由 MemoryEngine.__init__ 自动设置


class MemoryEngine:
    """白槿记忆引擎。五层全栈，32G 专属优化。"""

    def __init__(self):
        global _engine
        _engine = self
        self.db = Database()
        self.time_index = TimeIndex()
        self.vector_index = VectorIndex()
        self.bm25_index = BM25Index()
        self.associative_index = AssociativeIndex()
        self.category_store = CategoryStore()
        self.resource_store = ResourceStore()

        # 稀疏共现图：记忆之间的赫布连接，支持链式联想
        self.cooccurrence_graph = CooccurrenceGraph.load(COOCCURRENCE_PATH)
        # 记忆背景化器：每次对话自动生成潜意识语义云
        self.backgrounder = MemoryBackgrounder(self)

        self.retrieve = RetrieveEngine(
            self.db, self.time_index, self.vector_index,
            self.bm25_index, self.associative_index, self.category_store,
        )
        self.write_pipeline = WritePipeline(
            self.db, self.time_index, self.vector_index,
            self.bm25_index, self.associative_index,
            self.category_store, self.resource_store,
        )
        self.evolve = EvolutionEngine(
            self.db, self.time_index, self.vector_index,
            self.bm25_index, self.associative_index,
            self.category_store, self.resource_store,
        )
        self._started = False
        self._shutdown_done = False

    def start(self):
        if self._started:
            logger.warning("记忆引擎已启动，跳过重复启动")
            return
        logger.info("白槿记忆引擎启动中...")

        # 1. 类别层全量加载
        self.category_store.load_all()
        logger.info("类别层加载完成，%s 个文件", len(self.category_store))

        # 2. 时间轴加载
        items = self.db.load_all_items()
        self.time_index.load_all(items)
        logger.info("时间轴加载完成，%s 条", len(self.time_index))

        # 3. BM25 加载
        for it in items:
            self.bm25_index.add(it["id"], it["content"])
        logger.info("BM25 加载完成，%s 篇", len(self.bm25_index))

        # 4. 关联索引加载
        for it in items:
            kws = it.get("keywords", [])
            if isinstance(kws, str):
                import json
                try:
                    kws = json.loads(kws)
                except json.JSONDecodeError:
                    kws = []
            self.associative_index.add(it["id"], kws)
        logger.info("关联索引加载完成")

        # 5. FAISS：先试快照，失败则构建
        id_map = self.db.query("SELECT item_id, faiss_idx FROM id_mapping")
        snapshot_ok = self.vector_index.load_snapshot(
            {r["item_id"]: r["faiss_idx"] for r in id_map}
        )
        if snapshot_ok:
            logger.info("FAISS 快照加载完成")
        else:
            logger.info("FAISS 快照不存在，开始构建...")
            self.vector_index.build(
                [(it["id"], it["content"]) for it in items]
            )
            id_map = self.vector_index.save_snapshot()
            self.db.save_id_mapping(id_map)
            logger.info("FAISS 构建完成")

        # 6. 启动写入线程
        self.write_pipeline.start()

        # 7. 启动演化引擎
        self.evolve.start()

        # 8. 感官采集器热初始化（记录准确的 born_at）
        from .config import SENSE_ENABLE
        if SENSE_ENABLE:
            try:
                from .senses import get_sense_collector
                get_sense_collector()
                logger.info("感官采集器初始化成功")
            except Exception as e:
                logger.warning("感官采集器初始化失败: %s", e)

        # 9. 状态呼吸引擎热初始化
        try:
            from .state import get_breathing_state
            get_breathing_state()
            logger.info("状态呼吸引擎初始化成功")
        except Exception as e:
            logger.warning("状态呼吸引擎初始化失败: %s", e)

        # 10. 欲望系统热初始化 + 恢复持久化状态
        try:
            from .desire_system import get_desire_pool
            pool = get_desire_pool()
            pool.load_state()
            logger.info("欲望系统初始化成功, %d 个欲望", len(pool._desires))
        except Exception as e:
            logger.warning("欲望系统初始化失败: %s", e)

        # 11. 社交直觉热初始化（经验库 + FAISS 索引）
        try:
            from .common_sense import preload_experiences
            count = preload_experiences()
            logger.info("社交直觉初始化成功, %d 条经验", count)
        except Exception as e:
            logger.warning("社交直觉初始化失败: %s", e)

        # 12. 启动自检（待排查：主线程执行时与后台线程冲突）
        # self._startup_self_check()

        self._started = True
        logger.info("白槿记忆引擎启动完成")

    def _startup_self_check(self):
        """启动自检：逐个验证关键组件可用。"""
        from .utils import now_ts as _ts
        checks = []
        checks.extend(self._check_life_stream())
        checks.extend(self._check_desire_system(_ts))
        checks.extend(self._check_breathing_state())
        checks.extend(self._check_social_intuition())
        for name, ok, detail in checks:
            if ok:
                logger.info("启动自检通过: %s, %s", name, detail)
            else:
                logger.error("启动自检失败: %s, %s", name, detail)

    def _check_life_stream(self) -> list[tuple[str, bool, str]]:
        try:
            from .life_stream import get_life_stream_engine
            ok, detail = get_life_stream_engine().health_check()
            return [("LifeStream", ok, detail)]
        except Exception as e:
            return [("LifeStream", False, str(e))]

    def _check_desire_system(self, ts: int) -> list[tuple[str, bool, str]]:
        try:
            from .config import (
                SELF_CHECK_SOCIAL, SELF_CHECK_MOOD,
                SELF_CHECK_SENSE_FRESH, SELF_CHECK_ENERGY, SELF_CHECK_IS_LATE,
            )
            from .desire_system import get_desire_pool
            pool = get_desire_pool()
            pool.batch_accumulate(
                current_ts=ts, social_drive=SELF_CHECK_SOCIAL,
                sense_fresh=SELF_CHECK_SENSE_FRESH, assoc_weight=0.0,
                relation_score=0.0, mood=SELF_CHECK_MOOD,
                energy=SELF_CHECK_ENERGY, is_late=SELF_CHECK_IS_LATE,
            )
            return [("DesireSystem", True, f"{pool.desire_count}个欲望")]
        except Exception as e:
            return [("DesireSystem", False, str(e))]

    def _check_breathing_state(self) -> list[tuple[str, bool, str]]:
        try:
            from .state import get_breathing_state
            st = get_breathing_state()
            st.calc_depth_index()
            st.describe()
            return [("BreathingState", True, f"mood={st.mood:.2f}")]
        except Exception as e:
            return [("BreathingState", False, str(e))]

    def _check_social_intuition(self) -> list[tuple[str, bool, str]]:
        try:
            from .common_sense import list_experiences
            exps = list_experiences()
            count = len(exps)
            return [("SocialIntuition", True, f"{count}条经验")]
        except Exception as e:
            return [("SocialIntuition", False, str(e))]

    def shutdown(self):
        """安全关闭引擎：停后台线程、持久化索引和状态。"""
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        self.write_pipeline.stop()
        self.evolve.stop()
        self.db.flush()
        id_map = self.vector_index.save_snapshot()
        if id_map:
            self.db.save_id_mapping(id_map)
        try:
            self.cooccurrence_graph.consolidate()
            self.cooccurrence_graph.save(COOCCURRENCE_PATH)
        except Exception:
            logger.error("共现图保存失败", exc_info=True)
        try:
            from .desire_system import get_desire_pool
            get_desire_pool().save_state()
        except Exception:
            pass
        try:
            from .senses import get_sense_collector
            sc = get_sense_collector()
            sc._weather_running = False
        except Exception:
            pass
        self.db.close()
        logger.info("白槿记忆引擎已关闭")

    # ── 公开接口 ──

    def search_sync(self, query: str) -> list[dict]:
        results, _ = self.retrieve.search_sync(query)
        return results

    def remember(self, user_msg: str, assistant_reply: str,
                 cloud_snapshot: str | None = None,
                 emotion_event: str | None = None,
                 timestamp: int | None = None):
        """写入一条对话。"""
        if timestamp is not None:
            timestamp = validate_ts(timestamp)
        self.write_pipeline.write(user_msg, assistant_reply,
                                  cloud_snapshot=cloud_snapshot,
                                  emotion_event=emotion_event,
                                  timestamp=timestamp)

    def remember_sync(self, user_msg: str, assistant_reply: str,
                      cloud_snapshot: str | None = None,
                      emotion_event: str | None = None,
                      timestamp: int | None = None):
        """同 remember，同步版。"""
        if timestamp is not None:
            timestamp = validate_ts(timestamp)
        self.write_pipeline.write(user_msg, assistant_reply,
                                  cloud_snapshot=cloud_snapshot,
                                  emotion_event=emotion_event,
                                  timestamp=timestamp)

    def record_fact(self, content: str, ts: int | None = None) -> str:
        """写入一条 session 事实。type=session，grade=B，走全索引。"""
        if ts is not None:
            ts = validate_ts(ts)
        item_id = shortuuid.uuid()
        ts = ts or now_ts()
        keywords = self._extract_simple_keywords(content)
        labels = _keywords_to_labels(keywords)
        item = {
            "id": item_id, "resource_id": "", "type": "session",
            "content": content, "tag": "session", "grade": "B",
            "keywords": keywords, "labels": labels, "timestamp": ts,
            "created_at": ts, "updated_at": ts,
            "access_count": 0, "last_access": 0, "deleted": 0,
        }
        self.db.insert_item(item)
        self.write_pipeline.time_index.add(item_id, ts)
        self.write_pipeline.bm25_index.add(item_id, content)
        self.write_pipeline.vector_index.add(item_id, content)
        self.write_pipeline.associative_index.add(item_id, keywords)
        return item_id

    def record_impression(self, content: str, ts: int | None = None) -> str:
        """写入一条短期印象。type=impression，grade=C，7天自动过期。"""
        if ts is not None:
            ts = validate_ts(ts)
        item_id = shortuuid.uuid()
        ts = ts or now_ts()
        keywords = self._extract_simple_keywords(content)
        labels = _keywords_to_labels(keywords)
        item = {
            "id": item_id, "resource_id": "", "type": "impression",
            "content": content, "tag": "impression", "grade": "C",
            "keywords": keywords, "labels": labels, "timestamp": ts,
            "created_at": ts, "updated_at": ts,
            "access_count": 0, "last_access": 0, "deleted": 0,
        }
        self.db.insert_item(item)
        self.write_pipeline.time_index.add(item_id, ts)
        self.write_pipeline.bm25_index.add(item_id, content)
        self.write_pipeline.vector_index.add(item_id, content)
        self.write_pipeline.associative_index.add(item_id, keywords)
        return item_id

    @staticmethod
    def _extract_simple_keywords(text: str) -> list[str]:
        """从 session 事实中提取关键词。"""
        # 2-4 字中文短语
        words = re.findall(r'[一-鿿]{2,4}', text)
        # 去重 + 去停用词
        stop = {'的是','为了','以及','这个','那个','可以','使用','通过','进行','一个'}
        seen = set()
        result = []
        for w in words:
            if w not in stop and w not in seen:
                seen.add(w)
                result.append(w)
        return result[:10]
