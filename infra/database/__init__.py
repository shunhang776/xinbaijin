"""
infra.database — SQLite connection pool with WAL mode and incremental migrations.

Public API
----------
- ``DatabasePool`` — thread-safe connection pool (max 5, RLock), WAL with
  Windows fallback, auto-transaction on critical writes.
- ``get_connection`` — context manager that acquires a connection from the
  pool and automatically returns it on exit (instance method on DatabasePool).

Usage::

    from infra.database import DatabasePool

    pool = DatabasePool()

    # One-shot transactional write
    pool.execute("INSERT INTO items (id, content) VALUES (?, ?)", ("X", "hello"))

    # Multi-statement DDL
    pool.executescript("CREATE TABLE IF NOT EXISTS ...")

    # Low-level access (no auto-transaction — caller manages commit)
    with pool.get_connection() as conn:
        rows = conn.execute("SELECT * FROM items").fetchall()
"""

from __future__ import annotations

from infra.database.connection import DatabasePool

__all__ = ["DatabasePool", "db_pool", "get_connection"]

# Module-level singleton pool so that ``get_connection`` is a bound method.
#
# IMPORTANT: db_pool 在首次导入时初始化，需确保 core.config 已加载（在 infra 导入之前导入 core）。
# 这是启动顺序约定，不在此处做代码层面延迟初始化。
db_pool = DatabasePool()

# ``get_connection`` is a bound instance method on ``db_pool``, callable as
# ``get_connection()`` without an explicit ``self`` argument.
get_connection = db_pool.get_connection
