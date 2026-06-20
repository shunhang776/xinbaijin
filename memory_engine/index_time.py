"""
时间索引层 — UTC bisect 二分检索，全量内存常驻。
"""
import time
import re
import bisect
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from .config import RELATIVE_DAYS, WEEK_OFFSETS, PERIODS
from .utils import day_str

_UTC = timezone.utc


class TimeIndex:
    """全局时间轴。所有时间戳为 UTC epoch 秒。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._axis: list[tuple[int, str]] = []
        self._by_day: dict[str, list[str]] = defaultdict(list)
        self._item_index: dict[str, int] = {}

    # ── 加载 ──

    def load_all(self, items: list[dict]):
        with self._lock:
            self._axis = [(it["timestamp"], it["id"])
                          for it in items if it.get("timestamp")]
            self._axis.sort(key=lambda x: x[0])
            self._by_day.clear()
            self._item_index.clear()
            for i, (ts, iid) in enumerate(self._axis):
                self._by_day[day_str(ts)].append(iid)
                self._item_index[iid] = i

    # ── 检索 ──

    def search(self, query: str) -> list[str]:
        spans = self._rule_fallback(query)
        if not spans:
            spans = self._extract_time_spans(query)
        if not spans:
            return []
        results = []
        for start, end in spans:
            results.extend(self._bisect_range(start, end))
        return list(dict.fromkeys(results))

    def _extract_time_spans(self, query: str) -> list[tuple[int, int]]:
        """jionlp NER。不可用时返回空，上游走语义通道。"""
        try:
            import jionlp as jio
        except ImportError:
            return []
        res = jio.ner.extract_time(
            f"[时间]{query}", time_base=time.time(), with_parsing=False
        )
        if not res:
            return []
        return self._parse_jionlp_result(res)

    def _parse_jionlp_result(self, res: list) -> list[tuple[int, int]]:
        spans = []
        base = res[0]
        for t in res[1:]:
            try:
                # jionlp parse_time 需要 time_base 作为参照
                parsed = jio.parse_time(t["text"], time_base=base["text"])
                dt_start = _parse_dt(parsed["time"][0])
                dt_end = _parse_dt(parsed["time"][1])
                if dt_start and dt_end:
                    spans.append((int(dt_start.timestamp()),
                                  int(dt_end.timestamp())))
            except Exception:
                pass
        return spans

    def _bisect_range(self, start: int, end: int) -> list[str]:
        with self._lock:
            left = bisect.bisect_left(self._axis, (start, ""))
            right = bisect.bisect_right(self._axis, (end, "z" * 50))
            return [iid for (_, iid) in self._axis[left:right]]

    # ── 写入 ──

    def add(self, item_id: str, timestamp: int):
        with self._lock:
            pos = bisect.bisect_left(self._axis, (timestamp, item_id))
            self._axis.insert(pos, (timestamp, item_id))
            self._by_day[day_str(timestamp)].append(item_id)
            self._reindex_from(pos)

    def remove(self, item_id: str):
        with self._lock:
            if item_id not in self._item_index:
                return
            pos = self._item_index.pop(item_id)
            _, _ = self._axis.pop(pos)
            # 从 _by_day 中移除（遍历当天列表，通常很短）
            self._reindex_from(pos)

    def _reindex_from(self, pos: int):
        """从指定位置重建索引。O(n) 仅发生在写入路径。"""
        for i in range(pos, len(self._axis)):
            self._item_index[self._axis[i][1]] = i

    # ── 规则补全 ──

    @classmethod
    def _rule_fallback(cls, query: str) -> list[tuple[int, int]] | None:
        now = datetime.now(_UTC)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        for matcher in [cls._match_relative, cls._match_week, cls._match_period]:
            span = matcher(query, today)
            if span:
                return span
        return None

    @classmethod
    def _match_relative(cls, query: str, today: datetime):
        for phrase, days in RELATIVE_DAYS.items():
            if phrase in query:
                t = today - timedelta(days=days)
                return [(int(t.timestamp()),
                         int((t + timedelta(days=1)).timestamp()))]
        return None

    @classmethod
    def _match_week(cls, query: str, today: datetime):
        for phrase, days in WEEK_OFFSETS.items():
            if phrase in query:
                t = today - timedelta(days=days)
                mon = t - timedelta(days=t.weekday())
                return [(int(mon.timestamp()),
                         int((mon + timedelta(days=7)).timestamp()))]
        return None

    @classmethod
    def _match_period(cls, query: str, today: datetime):
        if re.search(r"\d+[年月日号]", query):
            return None
        for phrase, (sh, eh) in PERIODS.items():
            if phrase in query:
                s = today.replace(hour=sh)
                e = today.replace(hour=eh)
                if eh < sh:
                    e += timedelta(days=1)
                return [(int(s.timestamp()), int(e.timestamp()))]
        return None

    def __len__(self) -> int:
        return len(self._axis)


def _parse_dt(s: str) -> datetime | None:
    """解析 jionlp 返回的时间字符串 → UTC datetime。"""
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_UTC)
    except ValueError:
        return None
