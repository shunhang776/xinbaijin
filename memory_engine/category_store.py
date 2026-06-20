"""
类别层 — Markdown 文件全量内存常驻，自动同步磁盘修改。
"""
import time
import threading
from .config import CATEGORIES_DIR


class CategoryStore:
    """所有 .md 文件启动时全量加载，运行时零磁盘 IO。"""

    def __init__(self):
        CATEGORIES_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cache: dict[str, str] = {}
        self._mtime: dict[str, float] = {}

    def load_all(self):
        """启动时一次性全量加载。"""
        with self._lock:
            self._cache.clear()
            self._mtime.clear()
            for path in CATEGORIES_DIR.rglob("*.md"):
                rel = path.relative_to(CATEGORIES_DIR).as_posix()
                with open(path, "r", encoding="utf-8") as fp:
                    self._cache[rel] = fp.read()
                self._mtime[rel] = path.stat().st_mtime

    def read(self, rel_path: str) -> str:
        """读取类别文件。磁盘有更新时自动刷新。"""
        abs_path = CATEGORIES_DIR / rel_path
        with self._lock:
            if abs_path.exists():
                disk_mtime = abs_path.stat().st_mtime
                if disk_mtime > self._mtime.get(rel_path, 0):
                    with open(abs_path, "r", encoding="utf-8") as fp:
                        self._cache[rel_path] = fp.read()
                    self._mtime[rel_path] = disk_mtime
            return self._cache.get(rel_path, "")

    def read_relevant(self, query: str) -> str:
        """根据查询选择相关文件，返回拼接内容。"""
        paths = list(self._cache.keys())
        if not paths:
            return ""
        # 关键词匹配路径和内容，取前 3 个最相关的
        scored = []
        for p in paths:
            score = 0
            for kw in query:
                if kw in p.lower():
                    score += 2
                if kw in self._cache[p]:
                    score += 1
            if score > 0:
                scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        return "\n\n".join(self._cache[p] for _, p in scored[:3])

    def write(self, rel_path: str, content: str):
        """写入或更新一个类别文件，同时更新内存缓存。"""
        abs_path = CATEGORIES_DIR / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with open(abs_path, "w", encoding="utf-8") as fp:
                fp.write(content)
            self._cache[rel_path] = content
            self._mtime[rel_path] = time.time()

    def append(self, rel_path: str, entry: str):
        """向类别文件追加一条。"""
        existing = self.read(rel_path)
        self.write(rel_path, existing + entry + "\n")

    def __len__(self) -> int:
        return len(self._cache)
