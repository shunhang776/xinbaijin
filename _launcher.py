#!/usr/bin/env python3
"""
白槿 v2 Unified Launcher — ALL entry points MUST route through this file.

Startup sequence (in order, each step depends on the previous):
  1. ``import _env_setup`` — environment bootstrap. Runs before any
     other import so native libraries see the correct environment.
  2. ``sys.path`` — ensure the project root is on the import path.
  3. ``setup_root_logger()`` — install structured JSON log handlers.
  4. HTTP server — ASGI lifespan manages migrations + container start/stop
     + connection pool close. uvicorn handles signals natively.

Flags:
  --foreground-debug   Skip background-thread launch (useful for pdb/IDE
                       debugging and single-step tracing).

Environment:
  BAIJIN_CONFIG        Path to a ``.env``-style config file.  Consumed by
                       ``_env_setup`` before any other import — do NOT set
                       it after the process has started.
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════════
# 1. Environment bootstrap — MUST be the first executable line.
#    _env_setup resolves BAIJIN_CONFIG, sets encoding/threading vars,
#    and patches sys.path / os.environ before any library import sees
#    the stale state.
# ═══════════════════════════════════════════════════════════════════════
import _env_setup  # noqa: E402,F401

# ═══════════════════════════════════════════════════════════════════════
# 2. Project root on sys.path (idempotent).
# ═══════════════════════════════════════════════════════════════════════
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ═══════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════

def _build_lifespan():
    """返回 ASGI lifespan 上下文管理器，由 uvicorn 管理生命周期。

    uvicorn 启动时自动安装自己的 SIGINT/SIGTERM 处理器，
    我们必须通过 lifespan 的 startup/shutdown 钩子来执行
    迁移、容器启停、连接池关闭，避免信号处理器被覆盖。
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        # ── startup ──
        from core.logging import get_logger
        _log = get_logger(__name__)
        _log.info("执行数据库迁移…")
        from infra.database import db_pool
        from infra.database.migrations import run_migrations
        run_migrations(db_pool)

        _log.info("启动 DI 容器…")
        from core.container import container as di_container
        di_container.start_all()

        yield  # 服务运行中

        # ── shutdown ──
        _log.info("停止 DI 容器组件…")
        try:
            di_container.stop_all()
        except Exception:
            _log.exception("DI 停止阶段异常")

        _log.info("关闭数据库连接池…")
        try:
            db_pool.close()
        except Exception:
            _log.exception("数据库连接池关闭异常")

    return lifespan


def _start_http_server(debug: bool = False) -> None:
    """Launch the HTTP server.

    Prefer ``app.server.run_server()`` when the module exists (v2 target
    architecture).  Fall back to uvicorn + ``main:app`` for backward
    compatibility during the migration window.

    生命周期通过 ASGI lifespan 管理，uvicorn 自动处理信号。
    """
    try:
        import app.server  # noqa: F401
    except ImportError:
        app_server_available = False
    else:
        app_server_available = True

    lifespan = _build_lifespan()

    if app_server_available:
        from app.server import run_server
        run_server(debug=debug, lifespan=lifespan)
    else:
        import uvicorn
        import asyncio

        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        import main as _main_module
        old_app = _main_module.app

        # 直接注入 lifespan，完全保留原应用所有能力（路由/中间件/WebSocket/SSE）
        old_app.router.lifespan_context = lifespan

        uvicorn.run(
            old_app,
            host="127.0.0.1",
            port=8080,
            log_level="debug" if debug else "info",
            reload=False,
            workers=1,
        )


# ═══════════════════════════════════════════════════════════════════════
# main()
# ═══════════════════════════════════════════════════════════════════════

def main(*, debug: bool = False) -> None:
    """Execute the full startup sequence.

    生命周期管理：迁移 + 容器启停 + 连接池关闭 → ASGI lifespan，
    uvicorn 统一管理信号，不单独安装信号处理器。

    Parameters
    ----------
    debug : bool
        When ``True``, passed to uvicorn (``--foreground-debug``).
    """
    # ── 3. Structured logging ──────────────────────────────────────────
    from core.config import settings
    from core.logging import setup_root_logger, get_logger

    setup_root_logger(settings.log_level)
    _log = get_logger(__name__)
    _log.info(
        "白槿 v2 启动 (debug=%s config_version=%s)",
        debug, settings.config_version,
    )

    # ── 4. HTTP server（迁移 + 容器生命周期由 ASGI lifespan 管理）──────
    # 注意：不在 uvicorn 之前安装信号处理器 —— uvicorn 会覆盖信号处理器，
    # 改用 ASGI lifespan 的 startup/shutdown 钩子管理生命周期。
    _log.info("启动 HTTP 服务器…")
    _start_http_server(debug=debug)


# ═══════════════════════════════════════════════════════════════════════
# __main__ guard — Windows multiprocessing freeze support + CLI parsing
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    import multiprocessing

    multiprocessing.freeze_support()

    parser = argparse.ArgumentParser(
        description="白槿 v2 unified launcher — all entry points route through here.",
    )
    parser.add_argument(
        "--foreground-debug",
        action="store_true",
        dest="debug",
        help="Skip background thread launch (attach pdb / IDE debugger).",
    )
    args = parser.parse_args()

    main(debug=args.debug)
