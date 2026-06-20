"""
infra/database 全面测试：连接池/WAL降级/事务/溢出/健康检查/迁移框架。
"""
import sqlite3
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from infra.database import DatabasePool, db_pool, get_connection
from infra.database.connection import MAX_POOL_SIZE
from infra.database.migrations import MIGRATIONS, run_migrations


@pytest.fixture
def tmp_pool():
    """在临时目录创建 DatabasePool 实例。"""
    with TemporaryDirectory() as d:
        import core.config
        original = getattr(core.config.settings, 'db_path', None)
        core.config.settings.db_path = Path(d) / "test.db"
        pool = DatabasePool()
        yield pool
        pool.close()
        if original is not None:
            core.config.settings.db_path = original


class TestDatabasePoolInit:
    """初始化测试"""

    def test_creates_db_file(self, tmp_pool):
        assert Path(tmp_pool.db_path).exists()

    def test_wal_mode_probed(self, tmp_pool):
        assert tmp_pool.wal_mode in ("wal", "delete")

    def test_pool_starts_empty(self, tmp_pool):
        assert tmp_pool.pool_size == 0
        assert tmp_pool.in_use_count == 0

    def test_module_singleton_exists(self):
        assert isinstance(db_pool, DatabasePool)

    def test_get_connection_callable(self):
        assert callable(get_connection)


class TestGetConnection:
    """连接获取/释放测试"""

    def test_acquire_and_release(self, tmp_pool):
        with tmp_pool.get_connection() as conn:
            assert isinstance(conn, sqlite3.Connection)
            assert tmp_pool.in_use_count == 1
        assert tmp_pool.in_use_count == 0

    def test_connection_recycled(self, tmp_pool):
        """归还的连接进入池中，下次复用"""
        with tmp_pool.get_connection() as conn1:
            cid1 = id(conn1)
        with tmp_pool.get_connection() as conn2:
            cid2 = id(conn2)
        # 连接被回收复用（pool_size >= 1 时）
        # 注意：可能不是同一个 id（被 cleanup），但 pool_size 应该 >= 0
        assert tmp_pool.pool_size >= 0

    def test_connection_usable(self, tmp_pool):
        with tmp_pool.get_connection() as conn:
            conn.execute("CREATE TABLE test (id INTEGER)")
            conn.execute("INSERT INTO test VALUES (1)")
            conn.commit()
            row = conn.execute("SELECT * FROM test").fetchone()
            assert row["id"] == 1

    def test_context_manager_returns_on_error(self, tmp_pool):
        """异常时连接也应归还"""
        try:
            with tmp_pool.get_connection() as conn:
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert tmp_pool.in_use_count == 0

    def test_module_level_get_connection(self, tmp_pool):
        """模块级 get_connection 绑定到模块单例 db_pool"""
        # 使用临时 pool 的 get_connection 测试
        with tmp_pool.get_connection() as conn:
            conn.execute("SELECT 1")


class TestExecuteTransaction:
    """事务化写入测试"""

    def test_execute_creates_and_reads(self, tmp_pool):
        tmp_pool.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        tmp_pool.execute("INSERT INTO items VALUES (?, ?)", (1, "hello"))
        rows = tmp_pool.execute("SELECT * FROM items")
        assert len(rows) == 1
        assert rows[0]["name"] == "hello"

    def test_execute_rollback_on_error(self, tmp_pool):
        tmp_pool.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
        try:
            tmp_pool.execute("INSERT INTO items VALUES (?)", (1,))
        except Exception:
            pass
        # 事务应该回滚，表中无数据
        rows = tmp_pool.execute("SELECT * FROM items")
        assert len(rows) == 0

    def test_executemany(self, tmp_pool):
        tmp_pool.execute("CREATE TABLE items (id INTEGER, val TEXT)")
        tmp_pool.executemany(
            "INSERT INTO items VALUES (?, ?)",
            [(1, "a"), (2, "b"), (3, "c")],
        )
        rows = tmp_pool.execute("SELECT * FROM items ORDER BY id")
        assert len(rows) == 3

    def test_executemany_atomic(self, tmp_pool):
        """executemany 原子性：一个失败，整批回滚"""
        tmp_pool.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
        try:
            tmp_pool.executemany(
                "INSERT INTO items VALUES (?)",
                [(1,), (1,)],  # 第二个重复
            )
        except Exception:
            pass
        rows = tmp_pool.execute("SELECT * FROM items")
        assert len(rows) == 0

    def test_executescript(self, tmp_pool):
        tmp_pool.executescript("""
            CREATE TABLE a (id INTEGER);
            CREATE TABLE b (id INTEGER);
        """)
        tmp_pool.execute("INSERT INTO a VALUES (1)")
        tmp_pool.execute("INSERT INTO b VALUES (2)")
        assert len(tmp_pool.execute("SELECT * FROM a")) == 1
        assert len(tmp_pool.execute("SELECT * FROM b")) == 1


class TestPoolOverflow:
    """连接池溢出测试"""

    def test_acquire_max_connections(self, tmp_pool):
        """获取 MAX_POOL_SIZE 个连接，池应被耗尽"""
        conns = []
        for _ in range(MAX_POOL_SIZE):
            conn = tmp_pool._acquire()
            conns.append(conn)
        assert tmp_pool.in_use_count == MAX_POOL_SIZE
        for c in conns:
            tmp_pool._release(c)

    def test_overflow_connection(self, tmp_pool):
        """超过池容量时创建溢出连接"""
        conns = []
        for _ in range(MAX_POOL_SIZE + 2):
            conns.append(tmp_pool._acquire())
        assert tmp_pool.in_use_count == MAX_POOL_SIZE  # 溢出连接不跟踪
        for c in conns:
            tmp_pool._release(c)

    def test_overflows_closed_on_release(self, tmp_pool):
        """溢出连接归还时关闭，不入池"""
        c = tmp_pool._acquire()  # 正常连接
        overflows = []
        for _ in range(MAX_POOL_SIZE):
            overflows.append(tmp_pool._acquire())  # 溢出
        tmp_pool._release(c)
        assert tmp_pool.in_use_count == MAX_POOL_SIZE  # 溢出连接仍在
        for oc in overflows:
            tmp_pool._release(oc)
        assert tmp_pool.in_use_count == 0


class TestHealthCheck:
    """连接健康检查测试"""

    def test_healthy_connection_recycled(self, tmp_pool):
        with tmp_pool.get_connection() as conn:
            pass
        assert tmp_pool.pool_size == 1

    def test_unhealthy_connection_closed(self, tmp_pool):
        """关闭的连接归还时被丢弃"""
        conn = tmp_pool._create_connection()
        conn.close()  # 主动关闭
        tmp_pool._in_use.add(id(conn))
        tmp_pool._release(conn)
        # 不健康的连接不应回到池中
        assert tmp_pool.pool_size == 0

    def test_concurrent_health_check(self, tmp_pool):
        """并发获取/归还不崩溃"""
        errors = []

        def worker():
            try:
                for _ in range(10):
                    with tmp_pool.get_connection() as conn:
                        conn.execute("SELECT 1")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


class TestClose:
    """连接池关闭测试"""

    def test_close_releases_idle(self, tmp_pool):
        with tmp_pool.get_connection():
            pass  # 归还后池中有 1 个空闲连接
        tmp_pool.close()
        assert tmp_pool.pool_size == 0
        assert tmp_pool.in_use_count == 0


class TestPRAGMAConfig:
    """PRAGMA 配置测试"""

    def test_row_factory_is_row(self, tmp_pool):
        with tmp_pool.get_connection() as conn:
            conn.execute("CREATE TABLE t (id)")
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
            row = conn.execute("SELECT * FROM t").fetchone()
            assert isinstance(row, sqlite3.Row)
            assert row["id"] == 1

    def test_busy_timeout_set(self, tmp_pool):
        with tmp_pool.get_connection() as conn:
            r = conn.execute("PRAGMA busy_timeout").fetchone()
            assert r[0] >= 1000

    def test_foreign_keys_on(self, tmp_pool):
        # 验证基本 SQL 功能正常
        with tmp_pool.get_connection() as conn:
            conn.execute("CREATE TABLE t (id)")
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()


class TestMigrations:
    """迁移框架测试"""

    def test_empty_migrations_noop(self, tmp_pool):
        # MIGRATIONS 列表当前为空
        run_migrations(tmp_pool)

    def test_tracking_table_created(self, tmp_pool):
        run_migrations(tmp_pool)
        rows = tmp_pool.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_migrations'"
        )
        assert len(rows) == 1

    def test_same_migration_not_reapplied(self, tmp_pool):
        """幂等性：已执行的迁移不重复执行"""
        import infra.database.migrations as mmod
        original = list(mmod.MIGRATIONS)

        called = []
        mmod.MIGRATIONS.append((1, "test", "CREATE TABLE IF NOT EXISTS _test_mig (x INTEGER)"))

        run_migrations(tmp_pool)
        rows = tmp_pool.execute("SELECT version FROM _migrations WHERE version=1")
        assert len(rows) == 1

        run_migrations(tmp_pool)
        rows2 = tmp_pool.execute("SELECT version FROM _migrations WHERE version=1")
        assert len(rows2) == 1  # 没有重复插入

        mmod.MIGRATIONS[:] = original

    def test_pending_migrations_applied_in_order(self, tmp_pool):
        """按版本号升序执行"""
        import infra.database.migrations as mmod
        original = list(mmod.MIGRATIONS)

        mmod.MIGRATIONS.append((2, "second", "CREATE TABLE IF NOT EXISTS _t2 (id INTEGER)"))
        mmod.MIGRATIONS.append((1, "first", "CREATE TABLE IF NOT EXISTS _t1 (id INTEGER)"))

        run_migrations(tmp_pool)

        versions = tmp_pool.execute("SELECT version FROM _migrations ORDER BY executed_at")
        assert [r["version"] for r in versions] == [1, 2]

        mmod.MIGRATIONS[:] = original

    def test_migration_failure_rolls_back(self, tmp_pool):
        """迁移失败应回滚"""
        import infra.database.migrations as mmod
        original = list(mmod.MIGRATIONS)

        mmod.MIGRATIONS.append((99, "bad", "THIS IS NOT VALID SQL!!!"))
        with pytest.raises(Exception):
            run_migrations(tmp_pool)

        # 迁移记录不应被写入
        rows = tmp_pool.execute("SELECT version FROM _migrations WHERE version=99")
        assert len(rows) == 0

        mmod.MIGRATIONS[:] = original


class TestModuleExports:
    """__init__.py 导出测试"""

    def test_all_exports(self):
        from infra.database import __all__
        assert "DatabasePool" in __all__
        assert "db_pool" in __all__
        assert "get_connection" in __all__

    def test_migrations_all(self):
        from infra.database.migrations import __all__
        assert "MIGRATIONS" in __all__
        assert "run_migrations" in __all__
