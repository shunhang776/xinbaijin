"""
Database migration framework — versioned, ordered, idempotent.

How it works:
- Every migration is a ``(version, description, sql)`` tuple in the
  ``MIGRATIONS`` list.
- ``run_migrations(pool)`` creates the ``_migrations`` tracking table
  (if needed), queries already-applied versions, and runs any pending
  migrations in ascending version order.
- Each migration and its tracking insert execute inside a single
  transaction — applied or skipped atomically.
"""

from __future__ import annotations

from core.logging import get_logger
from core.utils import fmt_ts, now_ts

logger = get_logger(__name__)

__all__ = ["MIGRATIONS", "run_migrations"]

# ══════════════════════════════════════════════════════════════════════
# Migration registry
# ══════════════════════════════════════════════════════════════════════

MIGRATIONS: list[tuple[int, str, str]] = [
    # (version, description, sql_statement)
    # Add new migrations at the end in ascending version order.
    # Example:
    # (1, "create items table", "CREATE TABLE IF NOT EXISTS items (...);"),
]

# ══════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════


def run_migrations(pool: "DatabasePool") -> None:
    """Execute all pending migrations against *pool* in version order.

    Parameters
    ----------
    pool : DatabasePool
        The connection pool to run migrations against (imported from
        ``infra.database.connection``).
    """
    # ── ensure tracking table exists ─────────────────────────────────
    with pool.get_connection() as conn:
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _migrations (
                    version     INTEGER PRIMARY KEY,
                    description TEXT    NOT NULL,
                    executed_at TEXT    NOT NULL
                )
            """)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise

    # ── determine already-applied versions ───────────────────────────
    with pool.get_connection() as conn:
        try:
            rows = conn.execute("SELECT version FROM _migrations ORDER BY version").fetchall()
            applied: set[int] = {r["version"] for r in rows}
        except Exception:
            applied = set()

    # ── sort and filter ──────────────────────────────────────────────
    pending = [m for m in MIGRATIONS if m[0] not in applied]
    pending.sort(key=lambda m: m[0])

    if not pending:
        logger.debug("No pending migrations (applied: %d).", len(applied))
        return

    logger.info(
        "Pending migrations: %d (applied: %d, total registered: %d).",
        len(pending), len(applied), len(MIGRATIONS),
    )

    # ── execute each pending migration in its own transaction ────────
    for version, description, sql in pending:
        logger.info("Applying migration %d — %s ...", version, description)

        with pool.get_connection() as conn:
            try:
                conn.execute("BEGIN TRANSACTION")
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO _migrations (version, description, executed_at) "
                    "VALUES (?, ?, ?)",
                    (version, description, fmt_ts(now_ts())),
                )
                conn.commit()
                logger.info("Migration %d applied successfully.", version)
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.exception(
                    "Migration %d (%s) FAILED — aborting migration run.", version, description
                )
                raise

    logger.info("All %d pending migration(s) applied.", len(pending))
