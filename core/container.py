"""
白槿 v2 — 依赖注入容器。构造注入 + 拓扑排序生命周期编排。

使用约束：启动阶段单线程注册 + 启动，运行期只读 resolve。
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from typing import Any, Callable

from core.constants import SHUTDOWN_DRAIN_TIMEOUT
from core.exceptions import BaijinBaseError
from core.types import LifecycleComponent


class CircularDependencyError(BaijinBaseError):
    """循环依赖错误。"""


class DependencyContainer:
    """DI 容器。启动时注册，运行期禁止 resolve。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._registry: dict[type, Any] = {}
        self._factories: dict[type, Any] = {}
        self._deps: dict[type, list[type]] = {}
        self._lazy: set[type] = set()

    def register(self, protocol_type: type, factory: Callable[..., Any] | Any, deps: list[type] | None = None, lazy: bool = False) -> None:
        with self._lock:
            if protocol_type in self._factories:
                raise ValueError(f"重复注册: {protocol_type.__name__}")
            self._factories[protocol_type] = factory
            self._deps[protocol_type] = deps or []
            if lazy:
                self._lazy.add(protocol_type)

    def resolve(self, protocol_type: type) -> Any:
        with self._lock:
            if protocol_type in self._registry:
                return self._registry[protocol_type]
            factory = self._factories.get(protocol_type)
            if factory is None:
                raise KeyError(f"未注册: {protocol_type.__name__}")
            dep_instances = [self.resolve(dep) for dep in self._deps.get(protocol_type, [])]
            instance = factory(*dep_instances) if callable(factory) else factory
            self._registry[protocol_type] = instance
            return instance

    def _topo_order(self) -> list[type]:
        all_types = set(self._factories) | set(self._registry)
        in_degree: dict[type, int] = defaultdict(int)
        graph: dict[type, list[type]] = defaultdict(list)
        for t in all_types:
            in_degree.setdefault(t, 0)
        for t, deps in self._deps.items():
            for dep in deps:
                graph[dep].append(t)
                in_degree[t] += 1
        queue = deque(t for t in all_types if in_degree[t] == 0)
        result = []
        while queue:
            node = queue.popleft()
            result.append(node)
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        if len(result) != len(all_types):
            remaining = [t.__name__ for t in all_types if t not in result]
            raise CircularDependencyError(f"检测到循环依赖，未解析节点: {remaining}")
        return result

    def detect_cycle(self) -> None:
        with self._lock:
            self._topo_order()

    def start_all(self) -> None:
        with self._lock:
            order = self._topo_order()
        for protocol_type in order:
            if protocol_type in self._lazy:
                continue
            instance = self.resolve(protocol_type)
            if isinstance(instance, LifecycleComponent):
                instance.start()

    def stop_all(self, timeout: float = SHUTDOWN_DRAIN_TIMEOUT) -> None:
        from core.logging import get_logger
        _log = get_logger(__name__)
        with self._lock:
            try:
                order = list(reversed(self._topo_order()))
            except CircularDependencyError:
                _log.warning("检测到循环依赖，将按无序方式停止已实例化组件")
                order = list(self._registry.keys())
        for protocol_type in order:
            instance = self._registry.get(protocol_type)
            if isinstance(instance, LifecycleComponent):
                try:
                    instance.stop(timeout)
                except Exception:
                    _log.exception("停止组件失败: %s", protocol_type.__name__)

    def is_healthy(self) -> bool:
        with self._lock:
            for instance in self._registry.values():
                if isinstance(instance, LifecycleComponent):
                    if not instance.is_healthy():
                        return False
            return True


container = DependencyContainer()
