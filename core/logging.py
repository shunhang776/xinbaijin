"""
白槿 v2 — 结构化 JSON 日志。日志时间 UTC，业务时间戳北京时间。

架构：所有业务 logger 通过 get_logger() 纳入 baijin 命名空间，自动向上传播。
根 logger "baijin" 统一挂载控制台 + 文件 handler，子 logger 不单独配置。

注意：contextvars 在 asyncio 协程中自动跨任务传递 trace_id；
标准多线程（threading）场景下子线程不会自动继承，需手动 copy_context 或显式传入。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# ── 预编译脱敏正则 ──
_SENSITIVE_RE = re.compile(
    r'(?i)('
    r'api_key|secret|token|password|client_secret'
    r'|amap_key|memory_api_token|access_token|refresh_token'
    r')(\s*[=:]\s*)\S+'
)

__all__ = ["get_logger", "setup_root_logger", "setup_file_logging"]

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
span_id_var: ContextVar[str] = ContextVar("span_id", default="")

_ROOT_LOGGER_NAME = "baijin"
_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


class _JsonFormatter(logging.Formatter):
    """JSON 行格式化，自动注入 trace_id/span_id，过滤敏感字段。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _SENSITIVE_RE.sub(r'\1\2***', record.getMessage()),
            "trace_id": trace_id_var.get(),
            "span_id": span_id_var.get(),
            "module": f"{record.module}:{record.lineno}",
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False) + "\n"


def get_logger(name: str) -> logging.Logger:
    """获取日志器。name 通常传 __name__，自动纳入 baijin 命名空间。"""
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")


def setup_root_logger(level: int | str = logging.INFO) -> None:
    """初始化根 logger。每次调用更新级别；handler 幂等。"""
    if isinstance(level, str):
        level = _LEVELS.get(level.upper(), logging.INFO)

    root = logging.getLogger(_ROOT_LOGGER_NAME)
    root.setLevel(level)
    root.propagate = False

    if root.handlers:
        return  # handler 已安装，级别已在上面通过 root.setLevel 更新

    formatter = _JsonFormatter()
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 首次初始化时根据配置决定是否挂载文件日志
    from core.config import settings
    if settings.enable_file_logging:
        setup_file_logging()


def setup_file_logging(log_dir: str | Path | None = None) -> None:
    """挂载文件日志 handler（幂等）。"""
    if log_dir is None:
        from core.paths import LOGS_DIR
        log_dir = LOGS_DIR
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger(_ROOT_LOGGER_NAME)
    if not root.handlers:
        setup_root_logger()

    if any(isinstance(h, TimedRotatingFileHandler) for h in root.handlers):
        return

    formatter = _JsonFormatter()

    daily = TimedRotatingFileHandler(
        log_dir / "baijin.log",
        when="midnight", interval=1, backupCount=7,
        encoding="utf-8", utc=True,
    )
    daily.setFormatter(formatter)
    daily.setLevel(logging.DEBUG)
    root.addHandler(daily)

    error_handler = TimedRotatingFileHandler(
        log_dir / "baijin_error.log",
        when="midnight", interval=1, backupCount=30,
        encoding="utf-8", utc=True,
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)
    root.addHandler(error_handler)
