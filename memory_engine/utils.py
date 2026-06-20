"""
工具函数。全项目时间戳统一为北京时间秒级戳，格式化统一入口。
"""
import time as _time
from datetime import datetime, timezone, timedelta

_BEIJING = timezone(timedelta(hours=8))


def now_ts() -> int:
    """北京时间秒级戳。全项目唯一合法的时间戳生成入口。"""
    return int(_time.time() + 8 * 3600)


def ts_to_datetime(ts: int) -> datetime:
    """北京秒级戳 → datetime（北京时区）。全项目唯一合法的格式化入口。"""
    return datetime.fromtimestamp(ts - 8 * 3600, _BEIJING)


def validate_ts(ts: int) -> int:
    """校验外部传入的时间戳：必须是秒级，毫秒级直接报错。"""
    if ts >= 10 ** 12:
        raise ValueError(f"禁止毫秒时间戳: {ts}，请传秒级北京时间戳")
    return ts


def fmt_ts(ts: int) -> str:
    """北京时间戳 → ISO 格式字符串（仅用于日志展示）。"""
    return ts_to_datetime(ts).strftime("%Y-%m-%d %H:%M:%S")


def day_str(ts: int) -> str:
    """北京时间戳 → 'YYYY-MM-DD'（用于按天分组）。"""
    return ts_to_datetime(ts).strftime("%Y-%m-%d")


def week_str(ts: int) -> str:
    """北京时间戳 → 'YYYY-W##'（用于按周分组）。"""
    dt = ts_to_datetime(ts)
    return f"{dt.year}-W{dt.isocalendar()[1]:02d}"
