"""
白槿 v2 — 分层异常体系。

分层规则：
- 基础设施层 → InfraError (前缀 INF_)
- 记忆层 → MemoryError (前缀 MEM_)
- 插件层 → PluginError (前缀 PLG_)
- 应用层 → AppError (前缀 APP_，仅限路由/中间件/入口使用)

每层只抛自己的异常类型；上层可捕获并包装为 Result。
"""

from __future__ import annotations


class BaijinBaseError(Exception):
    """白槿异常基类。所有业务异常均继承此类。"""
    def __init__(self, message: str, error_code: str = "", context: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.context: dict[str, object] = context or {}

    def __str__(self) -> str:
        if self.error_code:
            return f"[{self.error_code}] {self.message}"
        return self.message

    def __repr__(self) -> str:
        return f"{type(self).__name__}(error_code={self.error_code!r}, message={self.message!r})"

    def with_context(self, **kwargs: object) -> BaijinBaseError:
        """链式追加上下文信息，返回自身。"""
        self.context.update(kwargs)
        return self


class InfraError(BaijinBaseError):
    """基础设施层异常。LLM/DB/嵌入/网络/配置错误均归此类。"""
    pass


class MemoryError(BaijinBaseError):
    """记忆层异常。索引/写入/检索/演化/未找到错误均归此类。"""
    pass


class PluginError(BaijinBaseError):
    """插件层异常。执行/校验/限流/重名/循环依赖错误均归此类。"""
    pass


class AppError(BaijinBaseError):
    """应用层异常。仅限路由/中间件/入口使用，下层严禁抛出此类。"""
    pass
