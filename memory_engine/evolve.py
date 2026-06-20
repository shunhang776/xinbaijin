"""
自主演化引擎。定时维护：清理/摘要/去重/升级/降级/归档/重建。
"""
import time
import logging
import threading
from datetime import datetime, timezone
from .config import (
    C_GRADE_EXPIRE_DAYS, RESOURCE_KEEP_DAYS,
    PROMOTE_ACCESS_COUNT, PROMOTE_WINDOW_DAYS,
    LIGHT_PROMOTE_COUNT, LIGHT_PROMOTE_WINDOW_DAYS,
    DEMOTE_NO_ACCESS_DAYS,
    CPU_BACKGROUND_MAX,
    DEDUP_THRESHOLD, MINHASH_NUM_PERM, DEDUP_WINDOW_DAYS,
    BACKUP_DIR, BACKUP_KEEP_DAYS,
    RESOURCES_DIR,
)
from .utils import now_ts, day_str

logger = logging.getLogger("memory.evolve")


class EvolutionEngine:
    """全自动记忆维护。CPU ≤ 30%，前台优先。"""

    def __init__(self, db, time_index, vector_index, bm25_index,
                 associative_index, category_store, resource_store):
        self.db = db
        self.time_index = time_index
        self.vector_index = vector_index
        self.bm25_index = bm25_index
        self.associative_index = associative_index
        self.category_store = category_store
        self.resource_store = resource_store
        self._lock = threading.RLock()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("演化引擎已启动")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=30)

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception:
                logger.exception("演化引擎异常")
            time.sleep(60)

    def _tick(self):
        if not self._cpu_ok():
            return
        now = datetime.now(timezone.utc)
        h, m, wd, d = now.hour, now.minute, now.weekday(), now.day
        if h == 3 and m < 5:
            self.cleanup_expired()
            self.cleanup_resources()
            self.backup()
        if h == 4 and m < 5:
            self.generate_daily_summary()
        if h == 5 and m < 5 and d % 3 == 0:
            self.light_maintenance()
        if h == 5 and m < 5 and wd == 6:
            self.full_maintenance()
        if h == 2 and m < 5 and d == 1:
            self.monthly_health_check()

    @staticmethod
    def _cpu_ok() -> bool:
        try:
            import psutil
            return psutil.cpu_percent(interval=0.5) <= CPU_BACKGROUND_MAX
        except ImportError:
            return True

    # ── 级联删除（db + 四个索引）──

    def _delete_cascade(self, iid: str):
        """删除一条记忆及其所有索引。加锁保证原子性。"""
        with self._lock:
            try:
                self.db.soft_delete(iid)
            except PermissionError:
                return
            self.time_index.remove(iid)
            self.bm25_index.remove(iid)
            self.vector_index.remove(iid)
            self.associative_index.remove(iid)

    # ── 每日 ──

    def cleanup_expired(self):
        cutoff = now_ts() - C_GRADE_EXPIRE_DAYS * 86400
        items = self.db.query(
            "SELECT id FROM items WHERE grade = 'C' AND last_access < ?",
            (cutoff,)
        )
        for it in items:
            self._delete_cascade(it["id"])
        logger.info("过期清理完成，%s 条", len(items))

    def cleanup_resources(self):
        """删除超过 RESOURCE_KEEP_DAYS 的原始对话 JSONL 文件。"""
        from datetime import timedelta
        beijing = timezone(timedelta(hours=8))
        cutoff_date = (datetime.now(beijing) - timedelta(days=RESOURCE_KEEP_DAYS)).strftime("%Y-%m-%d")
        if not RESOURCES_DIR.is_dir():
            return
        deleted = 0
        for fpath in RESOURCES_DIR.iterdir():
            fname = fpath.name
            if fname.endswith(".jsonl") and fname < f"{cutoff_date}.jsonl":
                try:
                    fpath.unlink()
                    deleted += 1
                except OSError:
                    pass
        if deleted:
            logger.info("资源清理完成，删除 %s 个旧文件", deleted)

    def generate_daily_summary(self):
        yesterday = day_str(now_ts() - 86400)
        records = self.resource_store.read_day(yesterday)
        if not records:
            return
        # resource_store 存的是 messages 列表，不是 content 字段
        lines = []
        for r in records[:50]:
            for msg in r.get("messages", [])[:4]:
                lines.append(msg.get("content", "")[:120])
        if not lines:
            return
        summary = f"## {yesterday}\n" + "\n".join(lines)
        self.category_store.write(f"events/{yesterday}.md", summary)
        logger.info("每日摘要完成: %s", yesterday)

    def backup(self):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        import shutil
        from .config import DB_PATH, FAISS_INDEX_PATH
        today = day_str(now_ts())
        for src in [DB_PATH, FAISS_INDEX_PATH]:
            if not src.exists():
                continue
            try:
                name = src.name
                dst = BACKUP_DIR / f"{today}_{name}"
                shutil.copy2(str(src), str(dst))
            except Exception:
                logger.exception("备份失败: %s", src)
        cutoff_day = day_str(now_ts() - BACKUP_KEEP_DAYS * 86400)
        for fpath in BACKUP_DIR.iterdir():
            if fpath.name.startswith(cutoff_day):
                try:
                    fpath.unlink()
                except OSError:
                    pass
        logger.info("备份完成: %s", today)

    # ── 每 3 天 ──

    def light_maintenance(self):
        recent = self.db.query(
            "SELECT id FROM items WHERE timestamp > ?",
            (now_ts() - LIGHT_PROMOTE_WINDOW_DAYS * 86400,)
        )
        for it in recent:
            item = self.db.get_item(it["id"])
            if item and item.get("access_count", 0) >= LIGHT_PROMOTE_COUNT:
                self.category_store.append(
                    f"{item['type']}.md",
                    f"- {item['content'][:200]}\n"
                )
        logger.info("轻量维护完成，%s 条", len(recent))

    # ── 每周 ──

    def _promote_frequent_items(self) -> int:
        """升级高频访问条目到类别层。返回升级数。"""
        promoted = self.db.query(
            "SELECT id FROM items WHERE access_count >= ? AND last_access > ?",
            (PROMOTE_ACCESS_COUNT, now_ts() - PROMOTE_WINDOW_DAYS * 86400)
        )
        for it in promoted:
            item = self.db.get_item(it["id"])
            if item:
                self.category_store.append(
                    f"{item['type']}.md",
                    f"- {item['content'][:200]}\n"
                )
        return len(promoted)

    def _demote_stale_items(self) -> int:
        """降级长期未访问条目为 C 级。返回降级数。"""
        demoted = self.db.query(
            "SELECT id FROM items WHERE last_access < ? AND grade != 'C'",
            (now_ts() - DEMOTE_NO_ACCESS_DAYS * 86400,)
        )
        for it in demoted:
            self.db.conn.execute(
                "UPDATE items SET grade = 'C', updated_at = ? WHERE id = ?",
                (now_ts(), it["id"])
            )
        self.db.conn.commit()
        return len(demoted)

    def full_maintenance(self):
        promoted = self._promote_frequent_items()
        demoted = self._demote_stale_items()
        self._deduplicate()
        logger.info("全量维护完成：升级%s 降级%s", promoted, demoted)

    def _deduplicate(self):
        try:
            from datasketch import MinHash, MinHashLSH
        except ImportError:
            return
        lsh = MinHashLSH(threshold=DEDUP_THRESHOLD, num_perm=MINHASH_NUM_PERM)
        minhashes = {}
        recent = self.db.query(
            "SELECT id, content FROM items WHERE timestamp > ? AND grade != 'C'",
            (now_ts() - DEDUP_WINDOW_DAYS * 86400,)
        )
        for item in recent:
            m = MinHash(num_perm=MINHASH_NUM_PERM)
            # 2-gram 分词后再 MinHash，中文去重才有效
            text = item["content"]
            for i in range(len(text) - 1):
                m.update(text[i:i + 2].encode("utf-8"))
            lsh.insert(item["id"], m)
            minhashes[item["id"]] = m

        for item in recent:
            dup_ids = lsh.query(minhashes[item["id"]])
            if len(dup_ids) > 1:
                root = min(dup_ids)
                for dup in dup_ids:
                    if dup != root:
                        self._delete_cascade(dup)

    # ── 每月 ──

    def monthly_health_check(self):
        items = self.db.load_all_items()
        if not items:
            return
        self.vector_index.rebuild(
            [(it["id"], it["content"]) for it in items]
        )
        id_map = self.vector_index.save_snapshot()
        if id_map:
            self.db.save_id_mapping(id_map)
        self.category_store.load_all()
        logger.info("月度健康检查完成，%s 条重建", len(items))
