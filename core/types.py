"""
白槿 v2 — 全项目 DTO 与 Protocol 唯一定义收口。

所有跨层数据结构和接口契约均在此定义。
DTO 使用 @dataclass(frozen=True) 防意外修改；
跨层接口使用 typing.Protocol 声明，不依赖具体实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar, runtime_checkable

from core.constants import MemoryGrade

T = TypeVar("T")


# ═══════════════════════════════════════════
# 通用返回类型
# ═══════════════════════════════════════════

@dataclass(frozen=True)
class Result(Generic[T]):
    """统一返回类型。ok=True 时 data 有效；ok=False 时 error_code 有效。"""
    ok: bool
    data: T | None = None
    error_code: str = ""
    error_detail: str = ""

    @classmethod
    def success(cls, data: T) -> Result[T]:
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, error_code: str, error_detail: str = "") -> Result[T]:
        return cls(ok=False, error_code=error_code, error_detail=error_detail)


# ═══════════════════════════════════════════
# 核心 DTO
# ═══════════════════════════════════════════

@dataclass(frozen=True)
class PromptData:
    """组装提示词所需的全部数据。"""
    persona: str = ""
    user_message: str = ""
    memories: list[str] = field(default_factory=list)
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryItem:
    """单条记忆记录。"""
    id: str  # noqa: A003 — dataclass 字段名，有意覆盖 built-in id()
    content: str
    grade: MemoryGrade = MemoryGrade.B
    created_at: int = 0
    updated_at: int = 0
    category: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = ""
    access_count: int = 0
    last_accessed_at: int = 0


@dataclass(frozen=True)
class HealthStatus:
    """健康检查状态。"""
    status: str = "ok"  # ok / degraded / error
    score: int = 100
    checks: dict[str, object] = field(default_factory=dict)
    version: str = "2.0.0"


@dataclass(frozen=True)
class MetricsSnapshot:
    """指标快照。"""
    timestamp: int = 0
    request_total: int = 0
    request_error: int = 0
    latency_avg_ms: float = 0.0
    latency_max_ms: float = 0.0
    cache_hit_rate: float = 0.0
    search_hit_rate: float = 0.0
    token_used: dict[str, int] = field(default_factory=dict)
    memory_mb: float = 0.0


@dataclass(frozen=True)
class TraceContext:
    """分布式追踪上下文。"""
    trace_id: str = ""
    span_id: str = ""


# ═══════════════════════════════════════════
# 跨层 Protocol
# ═══════════════════════════════════════════

@runtime_checkable
class DomainModule(Protocol):
    """领域模块协议。每个领域模块（identity/activity/habits 等）实现此接口。"""
    @property
    def name(self) -> str: ...
    @property
    def priority(self) -> int: ...
    def get_context(self) -> dict[str, object]: ...
    def get_tools(self) -> list[object]: ...


@runtime_checkable
class BaseIndex(Protocol):
    """索引协议。vector/bm25/time/associative 四种索引均实现此接口。"""
    @property
    def name(self) -> str: ...
    def add(self, item_id: str, text: str, meta: dict[str, object] | None = None) -> None: ...
    def remove(self, item_id: str) -> None: ...
    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]: ...
    def count(self) -> int: ...


@runtime_checkable
class FusionStrategy(Protocol):
    """多索引融合排序策略协议。"""
    @property
    def name(self) -> str: ...
    def fuse(self, results: list[list[tuple[str, float]]], weights: list[float], top_k: int) -> list[tuple[str, float]]: ...


@runtime_checkable
class LifecycleComponent(Protocol):
    """生命周期组件协议。DI 容器通过此接口管理启停。"""
    def start(self) -> None: ...
    def stop(self, timeout: float) -> None: ...
    def is_healthy(self) -> bool: ...


@runtime_checkable
class ActivityProvider(Protocol):
    """活动数据提供者协议。为 personality 模块提供用户活动数据。"""
    def get_recent_activity(self, hours: int) -> dict[str, object]: ...
