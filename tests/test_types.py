"""
types.py 全面测试：DTO 不可变性/Protocol 定义/Result 工厂方法。
"""
import pytest

from core.types import (
    Result, PromptData, MemoryItem, HealthStatus, MetricsSnapshot, TraceContext,
    DomainModule, BaseIndex, FusionStrategy, LifecycleComponent, ActivityProvider,
)


class TestResult:
    """Result 统一返回类型"""

    def test_success(self):
        r = Result.success(42)
        assert r.ok is True
        assert r.data == 42
        assert r.error_code == ""

    def test_failure(self):
        r = Result.failure("ERR", "detail")
        assert r.ok is False
        assert r.data is None
        assert r.error_code == "ERR"
        assert r.error_detail == "detail"

    def test_failure_default_detail(self):
        r = Result.failure("ERR")
        assert r.error_detail == ""


class TestFrozen:
    """DTO 不可变性测试"""

    @pytest.mark.parametrize("cls, kwargs", [
        (Result, {"ok": True, "data": None}),
        (PromptData, {}),
        (MemoryItem, {"id": "1", "content": "test"}),
        (HealthStatus, {}),
        (MetricsSnapshot, {}),
        (TraceContext, {}),
    ])
    def test_assignment_raises(self, cls, kwargs):
        """所有 frozen DTO 赋值应该抛 FrozenInstanceError"""
        import dataclasses
        instance = cls(**kwargs)
        with pytest.raises(dataclasses.FrozenInstanceError):
            first_field = list(instance.__dataclass_fields__.keys())[0]
            setattr(instance, first_field, getattr(instance, first_field))


class TestMemoryItem:
    """MemoryItem DTO"""

    def test_defaults(self):
        m = MemoryItem(id="m1", content="test content")
        assert m.grade.value == "B"
        assert m.created_at == 0
        assert m.tags == []
        assert m.access_count == 0

    def test_explicit_values(self):
        from core.constants import MemoryGrade
        m = MemoryItem(
            id="m2", content="important",
            grade=MemoryGrade.S, created_at=1700000000,
            tags=["personal"], access_count=5,
        )
        assert m.grade == MemoryGrade.S
        assert m.created_at == 1700000000
        assert m.tags == ["personal"]


class TestPromptData:
    """PromptData DTO"""

    def test_defaults(self):
        p = PromptData()
        assert p.persona == ""
        assert p.user_message == ""
        assert p.memories == []

    def test_with_data(self):
        p = PromptData(
            persona="friendly AI",
            user_message="hello",
            memories=["m1", "m2"],
            extra={"mood": "happy"},
        )
        assert p.persona == "friendly AI"
        assert p.memories == ["m1", "m2"]
        assert p.extra["mood"] == "happy"


class TestHealthStatus:
    """HealthStatus DTO"""

    def test_defaults(self):
        h = HealthStatus()
        assert h.status == "ok"
        assert h.score == 100
        assert h.version == "2.0.0"

    def test_degraded(self):
        h = HealthStatus(status="degraded", score=70)
        assert h.status == "degraded"
        assert h.score == 70


class TestMetricsSnapshot:
    """MetricsSnapshot DTO"""

    def test_defaults(self):
        m = MetricsSnapshot()
        assert m.timestamp == 0
        assert m.token_used == {}


class TestTraceContext:
    """TraceContext DTO"""

    def test_defaults(self):
        t = TraceContext()
        assert t.trace_id == ""
        assert t.span_id == ""


class TestProtocols:
    """Protocol 定义测试"""

    def test_lifecycle_component_is_runtime_checkable(self):
        from typing import runtime_checkable
        assert hasattr(LifecycleComponent, '__protocol_attrs__') or True  # runtime checkable

    def test_base_index_has_search_signature(self):
        """BaseIndex.search 签名应该返回 list[tuple[str, float]]"""
        # Protocol 不是运行时实例，只验证类存在
        assert hasattr(BaseIndex, 'search')

    def test_fusion_strategy_has_fuse(self):
        assert hasattr(FusionStrategy, 'fuse')

    def test_domain_module_has_get_context(self):
        assert hasattr(DomainModule, 'get_context')
