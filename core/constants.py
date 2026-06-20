"""
白槿 v2 — 业务常量与 API 路径收口。

所有 API 路径、错误码枚举、跨模块业务常量均在此定义。
禁止在其他模块硬编码 URL 或魔法数字。
"""

from __future__ import annotations

from enum import Enum


# ═══════════════════════════════════════════
# API 路径常量（全项目唯一收口）
# ═══════════════════════════════════════════

class APIPath(str, Enum):
    """API 路径枚举。所有路由注册必须引用此枚举，禁止硬编码字符串。"""
    _ignore_ = ["_PREFIX"]
    _PREFIX = "/memory/api"

    # 健康检查
    HEALTH = "/health"
    HEALTH_LIVENESS = "/health/liveness"
    HEALTH_READINESS = "/health/readiness"

    # QQ Bot
    QQ_WEBHOOK = "/qq/webhook"
    QQ_EVENT = "/qq/event"

    # 记忆 API（复用 _PREFIX）
    MEMORY_API_PREFIX = _PREFIX
    MEMORY_SEARCH = f"{_PREFIX}/search"
    MEMORY_WRITE = f"{_PREFIX}/write"
    MEMORY_UPDATE = f"{_PREFIX}/update"
    MEMORY_DELETE = f"{_PREFIX}/delete"
    MEMORY_STATS = f"{_PREFIX}/stats"

    # Claude 桥接
    BRIDGE_CHAT = "/bridge/chat"
    BRIDGE_STREAM = "/bridge/stream"

    # 内部管理
    ADMIN_RELOAD_CONFIG = "/admin/reload-config"
    ADMIN_SHADOW_MODE = "/admin/shadow-mode"

    # LLM 外部 API（全项目唯一 URL 收口）
    LLM_DEEPSEEK_CHAT = "https://api.deepseek.com/v1/chat/completions"
    LLM_QWEN_CHAT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


# ═══════════════════════════════════════════
# 错误码枚举
# ═══════════════════════════════════════════

class ErrorCode(str, Enum):
    """全项目错误码收口。按层级前缀分类。"""

    # ── 基础设施层 INF_ ──
    INF_TIMEOUT = "INF_TIMEOUT"
    INF_DB_ERROR = "INF_DB_ERROR"
    INF_EMBED_ERROR = "INF_EMBED_ERROR"
    INF_NETWORK_ERROR = "INF_NETWORK_ERROR"
    INF_CONFIG_ERROR = "INF_CONFIG_ERROR"
    INF_MODEL_NOT_LOADED = "INF_MODEL_NOT_LOADED"

    # ── 记忆层 MEM_ ──
    MEM_INDEX_ERROR = "MEM_INDEX_ERROR"
    MEM_WRITE_ERROR = "MEM_WRITE_ERROR"
    MEM_RETRIEVE_ERROR = "MEM_RETRIEVE_ERROR"
    MEM_EVOLVE_ERROR = "MEM_EVOLVE_ERROR"
    MEM_NOT_FOUND = "MEM_NOT_FOUND"
    MEM_DUPLICATE = "MEM_DUPLICATE"

    # ── 插件层 PLG_ ──
    PLG_EXEC_ERROR = "PLG_EXEC_ERROR"
    PLG_VALIDATION_ERROR = "PLG_VALIDATION_ERROR"
    PLG_RATE_LIMITED = "PLG_RATE_LIMITED"
    PLG_DUPLICATE_NAME = "PLG_DUPLICATE_NAME"
    PLG_CIRCULAR_DEPENDENCY = "PLG_CIRCULAR_DEPENDENCY"
    PLG_NOT_FOUND = "PLG_NOT_FOUND"

    # ── 应用层 APP_ ──
    APP_NOT_FOUND = "APP_NOT_FOUND"
    APP_AUTH_FAILED = "APP_AUTH_FAILED"
    APP_BAD_REQUEST = "APP_BAD_REQUEST"
    APP_INTERNAL_ERROR = "APP_INTERNAL_ERROR"
    APP_SERVICE_UNAVAILABLE = "APP_SERVICE_UNAVAILABLE"


# ═══════════════════════════════════════════
# 记忆等级
# ═══════════════════════════════════════════

class MemoryGrade(str, Enum):
    """记忆等级。S/A 级永不自动删除。"""
    S = "S"  # 核心人格
    A = "A"  # 重要
    B = "B"  # 普通
    C = "C"  # 临时/可过期


PROTECTED_GRADES: tuple[str, ...] = (MemoryGrade.A, MemoryGrade.S)


# ═══════════════════════════════════════════
# 业务常量
# ═══════════════════════════════════════════

# ── 时间（不可变数学常量） ──
SECONDS_PER_DAY: int = 86400
SECONDS_PER_HOUR: int = 3600

# ── 检索默认值（仅作文档参考，运行时以 config 为准） ──
MAX_MEMORY_TEXT_LEN: int = 2000

# ── 死信队列 ──
DEAD_LETTER_MAX_RETRIES: int = 3
DEAD_LETTER_RETRY_INTERVAL: int = 300
DEAD_LETTER_ARCHIVE_THRESHOLD: int = 100

# ── 缓存 ──
CACHE_DEFAULT_TTL: int = 300
CACHE_MAX_SIZE: int = 5000
CACHE_NULL_TTL: int = 30

# ── 优雅停止 ──
SHUTDOWN_DRAIN_TIMEOUT: float = 10.0
SHUTDOWN_FORCE_TIMEOUT: float = 15.0

# ── 限流 ──
RATE_LIMIT_DEFAULT_RPS: int = 10
RATE_LIMIT_BURST: int = 20

# ── 日志 ──
_MB: int = 1024 * 1024
LOG_RETENTION_DAYS: int = 7
LOG_MAX_BYTES: int = 10 * _MB
