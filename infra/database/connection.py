"""
Database connection pool — SQLite with WAL mode, thread-safe connection management.

Design:
- Connection pool (max 5) with RLock thread safety.
- WAL mode enabled by default; falls back to DELETE on incompatible Windows builds.
- Every critical write is auto-wrapped in BEGIN/COMMIT/ROLLBACK.
- Context-manager get_connection() acquires from pool, auto-returns on exit.
- Config sourced from core.config.settings and core.paths.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from core.config import settings
from core.logging import get_logger
from core.paths import DB_PATH
from core.exceptions import InfraError

logger = get_logger(__name__)

MAX_POOL_SIZE = 5


class DatabasePool:
    """SQLite connection pool.

    - Holds up to ``MAX_POOL_SIZE`` (5) idle connections in a deque.
    - ``_in_use`` tracks connection ids currently checked out.
    - All pool mutations are protected by ``threading.RLock``.
    - If the pool is exhausted an overflow connection is created (not pooled;
      closed immediately on release).
    """

    def __init__(self) -> None:
        # ── resolve db path ──────────────────────────────────────────
        db_path = getattr(settings, "db_path", DB_PATH)
        if not db_path or (isinstance(db_path, Path) and str(db_path) == "."):
            db_path = DB_PATH
        self._db_path: str = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        # ── pragma parameters ────────────────────────────────────────
        self._cache_mb: int = int(getattr(settings, "db_cache_size_mb", 2000))
        self._mmap_mb: int = int(getattr(settings, "db_mmap_size_mb", 2048))
        self._timeout_ms: int = int(getattr(settings, "db_busy_timeout_ms", 5000))

        # ── pool state ───────────────────────────────────────────────
        self._pool: deque[sqlite3.Connection] = deque()
        self._in_use: set[int] = set()
        self._lock: threading.RLock = threading.RLock()

        # ── journal mode ─────────────────────────────────────────────
        self._wal_mode: str = "delete"  # resolved in _init_wal_mode
        self._init_wal_mode()

        logger.info(
            "DatabasePool ready: db=%s wal=%s pool_max=%d cache=%dMB timeout=%dms",
            self._db_path, self._wal_mode, MAX_POOL_SIZE,
            self._cache_mb, self._timeout_ms,
        )

    # ══════════════════════════════════════════════════════════════════
    # WAL mode bootstrap
    # ══════════════════════════════════════════════════════════════════

    def _init_wal_mode(self) -> None:
        """Probe WAL mode with a temporary connection.

        On Windows with ancient VFS or network drives, WAL may be
        unsupported.  When that happens we silently fall back to DELETE
        and log a warning so the operator is aware.
        """
        probe = sqlite3.connect(self._db_path)
        try:
            probe.execute("PRAGMA journal_mode = WAL")
            row = probe.execute("PRAGMA journal_mode").fetchone()
            mode = row[0].lower() if row else "delete"
            if mode == "wal":
                self._wal_mode = "wal"
                logger.info("WAL mode active: %s", self._db_path)
            else:
                self._wal_mode = "delete"
                logger.warning(
                    "WAL mode not available (resolved '%s'), "
                    "falling back to DELETE.", mode,
                )
        except sqlite3.OperationalError as exc:
            self._wal_mode = "delete"
            logger.warning(
                "WAL mode unsupported on this platform: %s. "
                "Falling back to DELETE journal_mode.", exc,
            )
        finally:
            probe.close()

    # ══════════════════════════════════════════════════════════════════
    # Connection factory
    # ══════════════════════════════════════════════════════════════════

    def _create_connection(self) -> sqlite3.Connection:
        """Return a fresh, configured ``sqlite3.Connection``.

        Every connection receives the same PRAGMA profile so callers
        never need to configure a raw handle themselves.
        """
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row

        # journal mode (per-connection; _init_wal_mode already decided
        # the best mode for this database file)
        try:
            conn.execute(f"PRAGMA journal_mode = {self._wal_mode}")
        except sqlite3.OperationalError:
            pass

        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        conn.execute(f"PRAGMA cache_size = -{self._cache_mb * 1000}")
        conn.execute(f"PRAGMA mmap_size = {self._mmap_mb * 1024 * 1024}")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA wal_autocheckpoint = 1000")

        return conn

    # ══════════════════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════════════════

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context-managed connection from the pool.

        Usage::

            with pool.get_connection() as conn:
                rows = conn.execute("SELECT ...").fetchall()

        The connection is automatically health-checked and returned to
        the pool (or closed if unhealthy / overflow) when the block exits.
        """
        conn = self._acquire()
        try:
            yield conn
        finally:
            self._release(conn)

    def execute(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a single SQL statement inside a transaction.

        ``BEGIN TRANSACTION`` → execute → ``COMMIT``.  On any exception
        a ``ROLLBACK`` is attempted and the exception is re-raised.

        Returns
        -------
        list[dict]
            Rows returned by the statement.  For writes (INSERT / UPDATE /
            DELETE) this is typically an empty list; use ``get_connection()``
            directly if you need ``cursor.rowcount`` or ``cursor.lastrowid``
            after commit.
        """
        with self.get_connection() as conn:
            try:
                conn.execute("BEGIN TRANSACTION")
                cursor = conn.execute(sql, params)
                rows = [dict(r) for r in cursor.fetchall()]
                conn.commit()
                return rows
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

    def executemany(self, sql: str, params_list: list[tuple]) -> None:
        """Execute a single SQL against many parameter sets in one transaction.

        Transactions are atomic: if any row fails the entire batch is rolled back.
        """
        with self.get_connection() as conn:
            try:
                conn.execute("BEGIN TRANSACTION")
                conn.executemany(sql, params_list)
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

    def executescript(self, sql: str) -> None:
        """Execute a multi-statement SQL script inside a transaction.

        Useful for DDL (CREATE TABLE, CREATE INDEX, etc.).
        """
        with self.get_connection() as conn:
            try:
                conn.execute("BEGIN TRANSACTION")
                conn.executescript(sql)
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

    def close(self) -> None:
        """Close every idle connection in the pool.

        Connections still in-use are *not* force-closed — the caller
        should ensure no concurrent work is in flight before calling this.
        """
        with self._lock:
            closed = 0
            while self._pool:
                conn = self._pool.popleft()
                try:
                    conn.close()
                except Exception:
                    pass
                closed += 1
            self._pool.clear()
            self._in_use.clear()
            logger.info("DatabasePool closed (%d idle connections released).", closed)

    # ══════════════════════════════════════════════════════════════════
    # Introspection
    # ══════════════════════════════════════════════════════════════════

    @property
    def pool_size(self) -> int:
        """Number of connections currently sitting idle in the pool."""
        with self._lock:
            return len(self._pool)

    @property
    def in_use_count(self) -> int:
        """Number of connections currently checked out."""
        with self._lock:
            return len(self._in_use)

    @property
    def wal_mode(self) -> str:
        """Resolved journal mode: ``"wal"`` or ``"delete"``."""
        return self._wal_mode

    @property
    def db_path(self) -> str:
        """Absolute path to the database file."""
        return self._db_path

    # ══════════════════════════════════════════════════════════════════
    # Internal
    # ══════════════════════════════════════════════════════════════════

    def _acquire(self) -> sqlite3.Connection:
        """Take a connection from the pool (or create one).

        Called inside ``get_connection()`` — not part of the public API.
        """
        with self._lock:
            # Recycle an idle connection that is *not* still in_use
            while self._pool:
                conn = self._pool.popleft()
                conn_id = id(conn)
                if conn_id not in self._in_use:
                    self._in_use.add(conn_id)
                    return conn
                # Stale entry in deque — discard
                try:
                    conn.close()
                except Exception:
                    pass

            # Pool has capacity — create a fresh connection
            if len(self._in_use) < MAX_POOL_SIZE:
                conn = self._create_connection()
                self._in_use.add(id(conn))
                return conn

        # Pool exhausted — overflow connection (not tracked/pooled)
        logger.warning(
            "Connection pool exhausted (%d/%d in use), "
            "creating overflow connection.",
            len(self._in_use), MAX_POOL_SIZE,
        )
        return self._create_connection()

    def _release(self, conn: sqlite3.Connection) -> None:
        """Return *conn* to the pool or close it.

        Overflow connections (not in ``_in_use``) and unhealthy
        connections are closed instead of being returned to the pool.
        """
        conn_id = id(conn)
        with self._lock:
            if conn_id not in self._in_use:
                # Overflow connection — just close it
                try:
                    conn.close()
                except Exception:
                    pass
                return

            self._in_use.discard(conn_id)

            # Health check before recycling
            try:
                conn.execute("SELECT 1")
            except sqlite3.Error:
                try:
                    conn.close()
                except Exception:
                    pass
                return

            if len(self._pool) < MAX_POOL_SIZE:
                self._pool.append(conn)
                return

        # Pool full or health check failed — close
        try:
            conn.close()
        except Exception:
            pass
