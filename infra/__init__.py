"""
白槿 v2 — 基础设施层。

infra 层提供 LLM 客户端等基础能力，所有对外接口通过子模块暴露。
"""

from infra.llm_client import LLMClient, llm_client

__all__ = ["LLMClient", "llm_client"]
