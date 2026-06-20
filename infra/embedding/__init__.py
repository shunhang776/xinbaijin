"""
infra/embedding — BGE-M3 嵌入模块。

提供线程安全的 BGEEmbedder 单例，Query/Doc 分锁互不阻塞。
"""
from .embedder import BGEEmbedder, get_embedder

__all__ = ["BGEEmbedder", "get_embedder"]
