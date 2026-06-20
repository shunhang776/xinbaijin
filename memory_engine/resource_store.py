"""
资源层 — 原始对话按天写 JSONL。唯一不进内存的层。
"""
import os
import json
import threading
import logging
from .config import RESOURCES_DIR
from .utils import day_str, now_ts

logger = logging.getLogger("memory.resource")


class ResourceStore:
    """原始对话持久化。同步写入 ≤ 1ms，永不丢失原始数据。"""

    def __init__(self):
        RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def add(self, messages: list[dict], timestamp: int = None) -> str:
        """写入一条对话。返回 resource_id，用户立即收到回复。"""
        ts = timestamp or now_ts()
        rid = f"res_{ts}_{os.urandom(3).hex()}"
        record = {
            "id": rid,
            "timestamp": ts,
            "date": day_str(ts),
            "messages": messages,
        }
        day = day_str(ts)
        path = RESOURCES_DIR / f"{day}.jsonl"
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        return rid

    def read_day(self, date_str: str) -> list[dict]:
        """读取某天的全部原始对话。"""
        path = RESOURCES_DIR / f"{date_str}.jsonl"
        with self._lock:
            if not path.exists():
                return []
            records = []
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            logger.warning("跳过无效 JSONL 行: %s", line[:80])
                            continue
        return records

    def read_recent_days(self, n_days: int = 3) -> list[dict]:
        """读取最近 n 天全部原始对话，按时间升序。"""
        from datetime import datetime, timedelta, timezone
        beijing = timezone(timedelta(hours=8))
        today = datetime.now(beijing)
        all_records = []
        for i in range(n_days - 1, -1, -1):
            day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            all_records.extend(self.read_day(day))
        return all_records
