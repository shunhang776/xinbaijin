"""
container.py 全面测试：DI 容器注册/解析/拓扑排序/生命周期/循环检测。
"""
import pytest

from core.container import DependencyContainer, CircularDependencyError
from core.types import LifecycleComponent


class FakeComponent(LifecycleComponent):
    """测试用生命周期组件"""

    def __init__(self, name="test"):
        self.name = name
        self.started = False
        self.stopped = False
        self.healthy = True
        self._stop_timeout = None

    def start(self) -> None:
        self.started = True

    def stop(self, timeout: float) -> None:
        self.stopped = True
        self._stop_timeout = timeout

    def is_healthy(self) -> bool:
        return self.healthy


class FakeComponentA(FakeComponent):
    pass


class FakeComponentB(FakeComponent):
    pass


class FakeComponentC(FakeComponent):
    pass


class TestRegister:
    """注册测试"""

    def test_register_simple(self):
        dc = DependencyContainer()
        dc.register(FakeComponent, FakeComponent)
        instance = dc.resolve(FakeComponent)
        assert isinstance(instance, FakeComponent)

    def test_register_with_deps(self):
        dc = DependencyContainer()
        dc.register(FakeComponentA, FakeComponentA)
        dc.register(FakeComponentB, FakeComponentB, deps=[FakeComponentA])
        b = dc.resolve(FakeComponentB)
        assert isinstance(b, FakeComponentB)
        # A 也应该被自动 resolve
        a = dc.resolve(FakeComponentA)
        assert isinstance(a, FakeComponentA)

    def test_duplicate_register_raises(self):
        dc = DependencyContainer()
        dc.register(FakeComponent, FakeComponent)
        with pytest.raises(ValueError, match="重复注册"):
            dc.register(FakeComponent, FakeComponent)

    def test_register_factory_as_value(self):
        """factory 可以是值（非 callable），直接作为实例"""
        dc = DependencyContainer()
        instance = FakeComponent()
        dc.register(FakeComponent, instance)  # not callable
        assert dc.resolve(FakeComponent) is instance


class TestResolve:
    """解析测试"""

    def test_resolve_unregistered_raises(self):
        dc = DependencyContainer()
        with pytest.raises(KeyError, match="未注册"):
            dc.resolve(FakeComponent)

    def test_resolve_returns_same_instance(self):
        """同一类型 resolve 多次返回同一实例（单例）"""
        dc = DependencyContainer()
        dc.register(FakeComponent, FakeComponent)
        a = dc.resolve(FakeComponent)
        b = dc.resolve(FakeComponent)
        assert a is b

    def test_resolve_injects_deps(self):
        """resolve 时自动注入依赖"""
        dc = DependencyContainer()

        class Dep(FakeComponent):
            pass

        class Main(FakeComponent):
            pass

        created = []

        def factory_dep():
            created.append("dep")
            return Dep()

        def factory_main(dep):
            created.append("main")
            assert isinstance(dep, Dep)
            return Main()

        dc.register(Dep, factory_dep)
        dc.register(Main, factory_main, deps=[Dep])
        main = dc.resolve(Main)
        assert isinstance(main, Main)
        assert created == ["dep", "main"]

    def test_resolve_transitive_deps(self):
        """传递依赖：C→B→A"""
        dc = DependencyContainer()
        dc.register(FakeComponentA, FakeComponentA)
        dc.register(FakeComponentB, FakeComponentB, deps=[FakeComponentA])
        dc.register(FakeComponentC, FakeComponentC, deps=[FakeComponentB])
        c = dc.resolve(FakeComponentC)
        assert isinstance(c, FakeComponentC)
        # 所有依赖都应该被解析
        assert isinstance(dc.resolve(FakeComponentA), FakeComponentA)
        assert isinstance(dc.resolve(FakeComponentB), FakeComponentB)


class TestTopoOrder:
    """拓扑排序测试"""

    def test_simple_chain(self):
        dc = DependencyContainer()
        dc.register(FakeComponentA, FakeComponentA)
        dc.register(FakeComponentB, FakeComponentB, deps=[FakeComponentA])
        dc.register(FakeComponentC, FakeComponentC, deps=[FakeComponentB])
        order = dc._topo_order()
        # A 应该在 B 前面，B 应该在 C 前面
        idx_a = order.index(FakeComponentA)
        idx_b = order.index(FakeComponentB)
        idx_c = order.index(FakeComponentC)
        assert idx_a < idx_b < idx_c

    def test_fork_graph(self):
        """A → [B, C] 叉形依赖"""
        dc = DependencyContainer()
        dc.register(FakeComponentA, FakeComponentA)
        dc.register(FakeComponentB, FakeComponentB, deps=[FakeComponentA])
        dc.register(FakeComponentC, FakeComponentC, deps=[FakeComponentA])
        order = dc._topo_order()
        idx_a = order.index(FakeComponentA)
        idx_b = order.index(FakeComponentB)
        idx_c = order.index(FakeComponentC)
        assert idx_a < idx_b
        assert idx_a < idx_c

    def test_independent_components(self):
        """无依赖关系的组件可以任意顺序"""
        dc = DependencyContainer()
        dc.register(FakeComponentA, FakeComponentA)
        dc.register(FakeComponentB, FakeComponentB)
        order = dc._topo_order()
        assert len(order) == 2
        assert FakeComponentA in order
        assert FakeComponentB in order

    def test_already_resolved_included(self):
        """已解析的实例也参与拓扑排序"""
        dc = DependencyContainer()
        dc.register(FakeComponentA, FakeComponentA)
        dc.resolve(FakeComponentA)  # 提前解析
        dc.register(FakeComponentB, FakeComponentB, deps=[FakeComponentA])
        order = dc._topo_order()
        assert FakeComponentA in order
        assert FakeComponentB in order


class TestCycleDetection:
    """循环依赖检测"""

    def test_direct_cycle_detected(self):
        dc = DependencyContainer()
        dc.register(FakeComponentA, FakeComponentA, deps=[FakeComponentB])
        dc.register(FakeComponentB, FakeComponentB, deps=[FakeComponentA])
        with pytest.raises(CircularDependencyError):
            dc.detect_cycle()

    def test_indirect_cycle_detected(self):
        """A→B→C→A 三节点环"""
        dc = DependencyContainer()
        dc.register(FakeComponentA, FakeComponentA, deps=[FakeComponentB])
        dc.register(FakeComponentB, FakeComponentB, deps=[FakeComponentC])
        dc.register(FakeComponentC, FakeComponentC, deps=[FakeComponentA])
        with pytest.raises(CircularDependencyError):
            dc.detect_cycle()

    def test_no_cycle_passes(self):
        dc = DependencyContainer()
        dc.register(FakeComponentA, FakeComponentA)
        dc.register(FakeComponentB, FakeComponentB, deps=[FakeComponentA])
        dc.detect_cycle()  # 不抛异常

    def test_self_loop_detected(self):
        """组件依赖自身"""
        dc = DependencyContainer()
        dc.register(FakeComponentA, FakeComponentA, deps=[FakeComponentA])
        with pytest.raises(CircularDependencyError):
            dc.detect_cycle()


class TestLifecycle:
    """生命周期管理测试"""

    def test_start_all(self):
        dc = DependencyContainer()
        c = FakeComponent()
        dc.register(FakeComponent, c)
        dc.start_all()
        assert c.started

    def test_start_all_skips_lazy(self):
        dc = DependencyContainer()
        c = FakeComponent()
        dc.register(FakeComponent, c, lazy=True)
        dc.start_all()
        assert not c.started

    def test_stop_all(self):
        dc = DependencyContainer()
        c = FakeComponent()
        dc.register(FakeComponent, c)
        dc.start_all()
        dc.stop_all()
        assert c.stopped
        assert c._stop_timeout == pytest.approx(10.0)

    def test_stop_all_reverse_order(self):
        """停止顺序应该与启动顺序相反"""
        dc = DependencyContainer()
        a = FakeComponent("a")
        b = FakeComponent("b")
        dc.register(FakeComponentA, a)
        dc.register(FakeComponentB, b, deps=[FakeComponentA])
        dc.start_all()

        stop_order = []

        def stop_b(timeout):
            stop_order.append("B")

        def stop_a(timeout):
            stop_order.append("A")

        b.stop = stop_b
        a.stop = stop_a
        dc.stop_all()
        assert stop_order == ["B", "A"]  # B 先停，A 后停

    def test_stop_all_with_cycle_degradation(self):
        """循环依赖时降级为无序停止，不崩溃"""
        dc = DependencyContainer()
        a = FakeComponent("a")
        b = FakeComponent("b")
        dc.register(FakeComponentA, a, deps=[FakeComponentB])
        dc.register(FakeComponentB, b, deps=[FakeComponentA])
        # 不抛异常，降级停止
        dc.stop_all()

    def test_stop_all_component_exception_does_not_block_others(self):
        """一个组件停失败不影响其他组件"""
        dc = DependencyContainer()
        a = FakeComponent("a")
        b = FakeComponent("b")

        def failing_stop(timeout):
            raise RuntimeError("stop failed")

        a.stop = failing_stop
        dc.register(FakeComponentA, a)
        dc.register(FakeComponentB, b)
        dc.start_all()
        dc.stop_all()  # b 仍然被停了
        assert b.stopped


class TestIsHealthy:
    """健康检查测试"""

    def test_all_healthy(self):
        dc = DependencyContainer()
        c = FakeComponent()
        dc.register(FakeComponent, c)
        dc.resolve(FakeComponent)
        assert dc.is_healthy()

    def test_one_unhealthy(self):
        dc = DependencyContainer()
        c = FakeComponent()
        c.healthy = False
        dc.register(FakeComponent, c)
        dc.resolve(FakeComponent)
        assert not dc.is_healthy()

    def test_empty_container(self):
        dc = DependencyContainer()
        assert dc.is_healthy()
