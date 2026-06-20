"""
写入流水线。Step1 同步写 Resource → Step2 入队列 → 单线程后台处理。
崩溃恢复：全量覆盖快照，重启后自动重放未完成任务。
"""
import json
import re
import logging
import threading
import shortuuid
from queue import Queue
from collections import Counter
from .config import WRITE_MAX_RETRIES, WRITE_RETRY_DELAY_SEC, DATA_DIR
from .utils import now_ts
from .preferences import auto_extract_preference
from .emotion import save_emotion_snapshot

logger = logging.getLogger("memory.write")

_SNAPSHOT_PATH = DATA_DIR / "write_queue_snapshot.json"


def _keywords_to_labels(keywords) -> list[str]:
    """v1 标签提取：将关键词清洗后作为语义标签。
    过滤纯数字、单字、过短的碎片。后续可升级为 LLM 提取。"""
    try:
        kws = json.loads(keywords) if isinstance(keywords, str) else keywords
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(kws, list):
        return []
    labels = []
    for kw in kws:
        kw = str(kw).strip()
        # 过滤：纯数字、单字、纯标点
        if len(kw) < 2:
            continue
        if kw.isdigit():
            continue
        labels.append(kw)
    return labels[:5]


class WritePipeline:
    """异步写入：用户感知 ≤ 1ms，单线程后台处理，崩溃可恢复。"""

    def __init__(self, db, time_index, vector_index, bm25_index,
                 associative_index, category_store, resource_store):
        self.db = db
        self.time_index = time_index
        self.vector_index = vector_index
        self.bm25_index = bm25_index
        self.associative_index = associative_index
        self.category_store = category_store
        self.resource_store = resource_store
        self._queue: Queue = Queue()
        self._worker: threading.Thread | None = None
        self._running = False
        self._last_reply = ""
        self._snapshot_lock = threading.RLock()

    # ── 快照：全量覆盖，队列镜像 ──

    def _save_full_snapshot(self):
        """全量覆盖保存当前队列状态，原子写入防文件损坏。"""
        tmp_path = _SNAPSHOT_PATH.with_suffix(".tmp")
        with self._snapshot_lock:
            try:
                tasks = list(self._queue.queue)
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(tasks, f, ensure_ascii=False, separators=(",", ":"))
                tmp_path.replace(_SNAPSHOT_PATH)
            except Exception as e:
                logger.error("生成队列快照失败: %s", e)
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass

    def _load_full_snapshot(self):
        """启动时加载快照，恢复未完成任务。"""
        # 清理历史残留临时文件
        tmp_path = _SNAPSHOT_PATH.with_suffix(".tmp")
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                logger.warning("清理快照临时文件失败，文件可能被占用: %s", tmp_path)
        _SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not _SNAPSHOT_PATH.exists():
            return
        with self._snapshot_lock:
            try:
                with open(_SNAPSHOT_PATH, "r", encoding="utf-8") as f:
                    tasks = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.error("加载队列快照失败: %s", e)
                self._clear_snapshot()
                return
        if isinstance(tasks, list) and tasks:
            logger.info("从快照恢复 %s 条未完成写入", len(tasks))
            for task in tasks:
                self._queue.put(task)

    def _clear_snapshot(self):
        if _SNAPSHOT_PATH.exists():
            try:
                _SNAPSHOT_PATH.unlink()
            except OSError as e:
                logger.warning("清空快照失败: %s", e)

    # ── 生命周期 ──

    def start(self):
        if self._running:
            return
        self._load_full_snapshot()
        self._running = True
        self._worker = threading.Thread(target=self._worker_loop, daemon=False)
        self._worker.start()
        logger.info("写入线程已启动")

    def stop(self):
        self._queue.put(None)
        self._queue.join()
        self._running = False
        if self._worker:
            self._worker.join(timeout=30)
        self._clear_snapshot()
        logger.info("写入线程已停止")

    def write(self, user_msg: str, assistant_reply: str,
              timestamp: int = None, cloud_snapshot: str | None = None,
              emotion_event: str | None = None) -> str:
        ts = timestamp or now_ts()
        rid = self.resource_store.add([
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": assistant_reply},
        ], ts)
        task = {
            "user_msg": user_msg,
            "assistant_reply": assistant_reply,
            "timestamp": ts,
            "resource_id": rid,
            "retries": 0,
            "cloud_snapshot": cloud_snapshot,
            "emotion_event": emotion_event,
        }
        self._queue.put(task)
        self._save_full_snapshot()
        return rid

    # ── 工作循环 ──

    def _worker_loop(self):
        while True:
            task = self._queue.get()
            if task is None:
                self._queue.task_done()
                break
            success = False
            try:
                self._process_task(task)
                success = True
            except Exception:
                logger.exception("写入任务处理失败，保留任务等待重试")
            finally:
                self._queue.task_done()
                if success:
                    self._save_full_snapshot()
                else:
                    task["retries"] = task.get("retries", 0) + 1
                    if task["retries"] < WRITE_MAX_RETRIES:
                        self._queue.put(task)
                    else:
                        logger.error("任务重试次数耗尽，丢弃: %s", task.get("resource_id"))

    def _process_task(self, task: dict):
        user_msg = task["user_msg"]
        assistant_reply = task["assistant_reply"]
        ts = task["timestamp"]
        rid = task["resource_id"]
        cloud_snapshot = task.get("cloud_snapshot")
        emotion_event = task.get("emotion_event")

        # 偏好提取放最前：只需用户输入，不依赖索引写入
        auto_extract_preference(user_msg)

        item_id = shortuuid.uuid()
        tag = self._classify(user_msg, assistant_reply)
        keywords = self._extract_keywords(user_msg)
        grade = self._grade(user_msg, assistant_reply)
        content = f"用户：{user_msg}\n白槿：{assistant_reply}"

        # 语义标签：v1 用关键词清洗后作为标签（后续可升级为 LLM 提取）
        labels = _keywords_to_labels(keywords)

        item = {
            "id": item_id, "resource_id": rid, "type": tag,
            "content": content, "tag": tag, "grade": grade,
            "keywords": keywords, "labels": labels, "timestamp": ts,
            "created_at": ts, "updated_at": ts,
            "access_count": 0, "last_access": 0, "deleted": 0,
        }

        self._write_core(item)
        self.vector_index.add(item_id, content)

        # 情绪快照放最后：所有索引写入完成后再保存
        save_emotion_snapshot(self.db, user_msg, assistant_reply,
                                cloud_snapshot, emotion_event)
        # 失效检索缓存，新情绪立即生效
        from .engine import _engine
        if _engine:
            _engine.retrieve.invalidate_emotion_cache()

        # 共现图建边：新记忆与关键词重叠的已有记忆建立赫布连接
        self._add_cooccurrence_edges(item_id, keywords, _engine)

        # 隐式偏好：先存上一轮回复，再更新_last_reply，避免时序错位
        previous = self._last_reply
        self._last_reply = assistant_reply
        if previous:
            from .preferences import analyze_reaction, add_implicit_preference
            pref = analyze_reaction(user_msg, previous)
            if pref:
                add_implicit_preference(pref)

    def _write_core(self, item: dict):
        self.db.insert_item(item)
        self.time_index.add(item["id"], item["timestamp"])
        self.bm25_index.add(item["id"], item["content"])
        self.associative_index.add(item["id"], item.get("keywords", []))

    # ── 分类 / 关键词 / 分级 ──

    @staticmethod
    def _classify(user_msg: str, reply: str) -> str:
        text = user_msg + reply
        if any(k in text for k in ["喜欢", "讨厌", "爱", "怕", "想", "习惯"]):
            return "preference"
        if any(k in text for k in ["约定", "答应", "下次", "以后", "记住"]):
            return "commitment"
        if any(k in text for k in ["今天", "昨天", "刚才", "去了", "做了"]):
            return "event"
        if any(k in text for k in ["知道", "专业", "技能", "会", "学过"]):
            return "knowledge"
        return "context"

    @staticmethod
    def _extract_keywords(text: str) -> str:
        clean = re.sub(r"[^一-鿿]", "", text)
        kws = [clean[i:i + l] for i in range(len(clean))
               for l in (2, 3, 4) if i + l <= len(clean)]
        top = [w for w, _ in Counter(kws).most_common(5)]
        return json.dumps(top, ensure_ascii=False)

    @staticmethod
    def _add_cooccurrence_edges(item_id: str, keywords_json: str, engine):
        """新记忆写入后，与有关键词重叠的已有记忆建立共现边。"""
        if engine is None or not hasattr(engine, "cooccurrence_graph"):
            return
        try:
            kws = json.loads(keywords_json) if isinstance(keywords_json, str) else keywords_json
        except (json.JSONDecodeError, TypeError):
            return
        if not kws:
            return

        # 通过关联索引找到共享关键词的记忆
        related: set[str] = set()
        for kw in kws:
            ids = engine.associative_index.search(kw, top_k=5)
            related.update(ids)
        related.discard(item_id)

        if related:
            engine.cooccurrence_graph.add_connections_batch(
                item_id, list(related), strength=0.1)

    @staticmethod
    def _grade(user_msg: str, reply: str) -> str:
        text = user_msg + reply
        if any(k in text for k in ["约定", "答应", "记住", "重要", "承诺"]):
            return "A"
        if len(text) < 30:
            return "C"
        return "B"
