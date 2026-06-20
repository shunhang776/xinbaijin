"""
数据库层。WAL 模式天然支持读写并发，写操作全事务化。
"""
import sqlite3
import json
import threading
import logging
from collections import deque
from .config import (
    DB_PATH, DB_CACHE_SIZE_MB, DB_MMAP_SIZE_MB,
    DB_BUSY_TIMEOUT_MS, DB_ITEMS_PER_PAGE, PROTECTED_GRADES,
)
from .utils import now_ts

logger = logging.getLogger("memory.db")


def _fix_beijing_time(db):
    """迁移 002：旧 UTC 时间戳全部 +28800 对齐北京时间。"""
    try:
        db.conn.execute("BEGIN TRANSACTION")
        for table, cols in [
            ("items", ["timestamp", "created_at", "updated_at"]),
            ("facts", ["timestamp"]),
            ("_migrations", ["executed_at"]),
        ]:
            for col in cols:
                db.conn.execute(
                    f"UPDATE {table} SET {col} = {col} + 28800 "
                    f"WHERE {col} > 1000000000"
                )
        db.conn.execute(
            "UPDATE items SET last_access = last_access + 28800 "
            "WHERE last_access > 0"
        )
        db.conn.commit()
        logger.info("DB 北京时间迁移完成")
    except sqlite3.OperationalError as e:
        try:
            db.conn.rollback()
        except Exception:
            pass
        logger.warning("DB 北京时间迁移失败: %s", e)


def _parse_json_array(raw) -> list:
    """安全解析 JSON 数组字符串，失败返回空列表。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _safe_json_dumps(value, default="[]") -> str:
    """安全序列化为 JSON 数组字符串。保证返回值一定是合法 JSON 数组。
    非法输入（普通字符串、对象、None 等）统一返回 default。"""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return json.dumps(parsed, ensure_ascii=False)
            return default  # JSON 对象/数字/字符串 → 无效
        except json.JSONDecodeError:
            return default  # "hello" 之类 → 无效
    # 非字符串：序列化后验证
    try:
        result = json.dumps(value, ensure_ascii=False)
        parsed = json.loads(result)
        return result if isinstance(parsed, list) else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


class Database:
    """封装所有数据库操作。实例化后持有单连接，WAL 模式保证并发安全。"""

    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = DB_PATH
        self._cache_mb = DB_CACHE_SIZE_MB
        self._mmap_mb = DB_MMAP_SIZE_MB
        self._timeout_ms = DB_BUSY_TIMEOUT_MS
        # 每线程独立连接，WAL 模式下多连接天然支持读写并发
        self._local = threading.local()
        self._init_tables()
        self._migrate()
        # 更新操作队列，批量提交减少磁盘 IO
        self._access_updates: deque[tuple] = deque()
        self._flush_threshold = 100

    @property
    def conn(self):
        """获取当前线程的数据库连接，自动创建。"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute(f"PRAGMA cache_size = -{self._cache_mb * 1000}")
            conn.execute("PRAGMA temp_store = MEMORY")
            conn.execute(f"PRAGMA mmap_size = {self._mmap_mb * 1024 * 1024}")
            conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
            self._local.conn = conn
        return self._local.conn

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                id          TEXT PRIMARY KEY,
                resource_id TEXT NOT NULL,
                type        TEXT NOT NULL,
                content     TEXT NOT NULL,
                tag         TEXT,
                grade       TEXT DEFAULT 'B',
                keywords    TEXT DEFAULT '[]',
                labels      TEXT DEFAULT '[]',
                timestamp   INTEGER NOT NULL,
                created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
                updated_at  INTEGER NOT NULL DEFAULT (unixepoch()),
                access_count INTEGER DEFAULT 0,
                last_access  INTEGER DEFAULT 0,
                deleted     INTEGER DEFAULT 0
            );
            -- 清理旧冗余索引
            DROP INDEX IF EXISTS idx_items_type;
            DROP INDEX IF EXISTS idx_items_type_ts;
            DROP INDEX IF EXISTS idx_items_deleted;
            -- 当前索引
            CREATE INDEX IF NOT EXISTS idx_items_ts ON items(timestamp);
            CREATE INDEX IF NOT EXISTS idx_items_type_deleted_ts ON items(type, deleted, timestamp);
            CREATE INDEX IF NOT EXISTS idx_items_grade ON items(grade);

            CREATE TABLE IF NOT EXISTS facts (
                id          TEXT PRIMARY KEY,
                subject     TEXT NOT NULL,
                predicate   TEXT NOT NULL,
                object      TEXT NOT NULL,
                confidence  REAL DEFAULT 1.0,
                source_item TEXT,
                timestamp   INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS id_mapping (
                item_id TEXT PRIMARY KEY,
                faiss_idx INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS _migrations (
                id          TEXT PRIMARY KEY,
                executed_at INTEGER NOT NULL
            );
        """)

    # ── 迁移系统 ──

    # 每个迁移：(id, sql, fixup_fn)
    # id: 唯一标识，执行后写入 _migrations 表
    # sql: 执行的 DDL
    # fixup_fn: 迁移成功后的数据修复回调(callable or None)，接收 Database 实例
    _MIGRATIONS = [
        ("001_add_labels", "ALTER TABLE items ADD COLUMN labels TEXT DEFAULT '[]'", None),
        ("002_beijing_time", "SELECT 1", _fix_beijing_time),
    ]

    def _migrate(self):
        """增量迁移：按 id 记录已执行迁移，只执行未跑过的。失败不阻塞启动。"""

        # 查询已执行的迁移
        try:
            done = {r["id"] for r in self.conn.execute(
                "SELECT id FROM _migrations").fetchall()}
        except sqlite3.OperationalError:
            done = set()

        # 按迁移 ID 数字前缀排序（不修改原类变量）
        migrations = sorted(self._MIGRATIONS, key=lambda x: int(x[0].split("_")[0]))

        pending = [m for m in migrations if m[0] not in done]
        logger.info("开始执行数据库增量迁移，待执行: %d个", len(pending))

        for mig_id, sql, fixup in migrations:
            if mig_id in done:
                continue
            try:
                # 事务：SQL 执行 + 迁移记录原子提交
                self.conn.execute("BEGIN TRANSACTION")
                self.conn.execute(sql)
                self.conn.execute(
                    "INSERT INTO _migrations (id, executed_at) VALUES (?, ?)",
                    (mig_id, int(now_ts())))
                self.conn.commit()
                logger.info("DB 迁移完成 [%s]: %s", mig_id, sql[:50])
                # 数据修复在事务外执行（可能耗时较长）
                if fixup:
                    fixup(self)
            except sqlite3.OperationalError as e:
                try:
                    self.conn.rollback()
                except Exception:
                    pass
                logger.warning("DB 迁移跳过 [%s]: %s", mig_id, e)

        # 一次性旧数据 NULL 修复（仅在从无迁移系统升级时执行）
        if not done:
            self._repair_null_legacy()

    def _repair_null_legacy(self):
        """旧库升级：修复 NULL keywords/labels。事务保护，原子执行。"""
        try:
            has_kw = self.conn.execute(
                "SELECT 1 FROM items WHERE keywords IS NULL LIMIT 1").fetchone()
            has_lb = self.conn.execute(
                "SELECT 1 FROM items WHERE labels IS NULL LIMIT 1").fetchone()
            if not has_kw and not has_lb:
                return
            # 事务：两个 UPDATE 要么全成功要么全失败
            self.conn.execute("BEGIN TRANSACTION")
            fixed_kw = self.conn.execute(
                "UPDATE items SET keywords = '[]' WHERE keywords IS NULL"
            ).rowcount
            fixed_lb = self.conn.execute(
                "UPDATE items SET labels = '[]' WHERE labels IS NULL"
            ).rowcount
            self.conn.commit()
            logger.info("DB 旧数据修复完成: keywords=%d条, labels=%d条",
                        fixed_kw, fixed_lb)
        except sqlite3.OperationalError as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            logger.warning("DB 旧数据修复失败: %s", e)

    # ── 写操作（事务化）──

    def insert_item(self, item: dict):
        """新增或更新一条记忆。失败自动回滚，异常向上抛。"""
        keywords = _safe_json_dumps(item.get("keywords", []))
        labels = _safe_json_dumps(item.get("labels", []))
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO items
                (id, resource_id, type, content, tag, grade, keywords, labels,
                 timestamp, created_at, updated_at, access_count, last_access, deleted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item["id"], item.get("resource_id", ""), item.get("type", "other"),
                item.get("content", ""), item.get("tag", ""), item.get("grade", "B"),
                keywords or "[]", labels or "[]",
                item.get("timestamp", 0),
                item.get("created_at", now_ts()),
                item.get("updated_at", now_ts()),
                item.get("access_count", 0), item.get("last_access", 0), 0
            ))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            logger.exception("insert_item 写入失败: %s", item.get("id"))
            raise

    def soft_delete(self, iid: str):
        """软删除。A/S 级记忆拒绝删除，抛异常上报。"""
        grade = self.conn.execute(
            "SELECT grade FROM items WHERE id = ?", (iid,)
        ).fetchone()
        if grade and grade[0] in PROTECTED_GRADES:
            raise PermissionError(
                f"受保护记忆不可删除: {iid} (grade={grade[0]})")
        try:
            self.conn.execute(
                "UPDATE items SET deleted = 1, updated_at = ? WHERE id = ?",
                (now_ts(), iid)
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            logger.exception("soft_delete 失败: %s", iid)
            raise

    def insert_fact(self, fact: dict):
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO facts
                (id, subject, predicate, object, confidence, source_item, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                fact["id"], fact["subject"], fact["predicate"], fact["object"],
                fact.get("confidence", 1.0), fact.get("source_item", ""),
                fact.get("timestamp", 0)
            ))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            logger.exception("insert_fact 失败: %s", fact.get("id"))
            raise

    # ── 读操作 ──

    def get_item(self, iid: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM items WHERE id = ? AND deleted = 0", (iid,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        # keywords：强制初始化为列表，处理 NULL / 非字符串类型
        d["keywords"] = _parse_json_array(d.get("keywords"))
        # labels：同上（旧数据迁移后为 '[]'，新数据直接有值）
        d["labels"] = _parse_json_array(d.get("labels"))
        self._queue_access_update(iid)
        return d

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def load_all_items(self) -> list[dict]:
        """分页加载全量未删除记忆，避免单次查询 OOM。"""
        items: list[dict] = []
        offset = 0
        while True:
            page = self.query(
                "SELECT * FROM items WHERE deleted = 0 "
                "ORDER BY timestamp LIMIT ? OFFSET ?",
                (DB_ITEMS_PER_PAGE, offset)
            )
            if not page:
                break
            items.extend(page)
            offset += DB_ITEMS_PER_PAGE
        return items

    def load_all_facts(self) -> list[dict]:
        return self.query("SELECT * FROM facts ORDER BY timestamp")

    def save_id_mapping(self, mapping: dict[str, int]):
        try:
            for iid, idx in mapping.items():
                self.conn.execute(
                    "INSERT OR REPLACE INTO id_mapping (item_id, faiss_idx) "
                    "VALUES (?, ?)", (iid, idx))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def load_id_mapping(self) -> dict[str, int]:
        rows = self.query("SELECT * FROM id_mapping")
        return {r["item_id"]: r["faiss_idx"] for r in rows}

    # ── 访问计数（批量刷盘，检索路径零磁盘 IO）──

    def _queue_access_update(self, iid: str):
        """检索命中时入队，达到阈值后批量刷盘。"""
        self._access_updates.append((now_ts(), iid))
        if len(self._access_updates) >= self._flush_threshold:
            self._flush_access_updates()

    def _flush_access_updates(self):
        if not self._access_updates:
            return
        updates = list(self._access_updates)
        self._access_updates.clear()
        try:
            self.conn.executemany(
                "UPDATE items SET access_count = access_count + 1, "
                "last_access = ?, updated_at = ? WHERE id = ?",
                [(t, t, iid) for t, iid in updates]
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            # 刷盘失败时把更新放回队列，下次再试
            self._access_updates.extendleft(reversed(updates))
            logger.warning("访问计数批量刷盘失败，已放回队列 (%s 条)", len(updates))

    def flush(self):
        """关闭前刷盘。"""
        self._flush_access_updates()

    def close(self):
        self.flush()
        self.conn.close()
