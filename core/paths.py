"""
白槿 v2 — 文件路径常量。

与 core/config.py 的 pydantic-settings 解耦，供无需依赖配置系统的底层模块使用。
config.py 中的路径字段保持同步，应用层优先使用 config 中的值。
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "BASE_DIR", "DATA_DIR",
    "DB_PATH", "FAISS_INDEX_PATH", "COOCCURRENCE_PATH",
    "SOCIAL_INTUITION_PATH", "SOCIAL_INTUITION_FAISS_PATH",
    "CATEGORIES_DIR", "RESOURCES_DIR", "BACKUP_DIR",
    "DESIRE_PERSIST_PATH",
    "LOGS_DIR", "PERSONA_PATH",
    "DEAD_LETTER_DIR", "SNAPSHOT_DIR",
]

# ── 根目录 ──
BASE_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = BASE_DIR / "data"

# ── 数据库与索引 ──
DB_PATH: Path = DATA_DIR / "memory.db"
FAISS_INDEX_PATH: Path = DATA_DIR / "faiss.index"
COOCCURRENCE_PATH: Path = DATA_DIR / "cooccurrence.pkl"

# ── 社交直觉 ──
SOCIAL_INTUITION_PATH: Path = DATA_DIR / "social_intuition.json"
SOCIAL_INTUITION_FAISS_PATH: Path = DATA_DIR / "social_intuition.faiss"

# ── 存储目录 ──
CATEGORIES_DIR: Path = DATA_DIR / "categories"
RESOURCES_DIR: Path = DATA_DIR / "resources"
BACKUP_DIR: Path = DATA_DIR / "backup"

# ── 持久化文件 ──
DESIRE_PERSIST_PATH: Path = DATA_DIR / "desire_state.json"

# ── 日志与配置 ──
LOGS_DIR: Path = BASE_DIR / "logs"
PERSONA_PATH: Path = BASE_DIR / "persona.txt"

# ── 运行时目录（不提交版本控制） ──
DEAD_LETTER_DIR: Path = DATA_DIR / "dead_letter"
SNAPSHOT_DIR: Path = DATA_DIR / "snapshots"
