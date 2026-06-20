"""
utils.py 全面测试：时间戳生成/转换/格式化/校验/往返一致性。
"""
import time as _time
from datetime import datetime, timezone, timedelta

import pytest

from core.utils import (
    now_ts, ts_to_datetime, validate_ts,
    fmt_ts, day_str, week_str,
    utc_ts_to_bj, bj_ts_to_utc,
)

_BEIJING = timezone(timedelta(hours=8))


class TestNowTs:
    """now_ts() 生成测试"""

    def test_returns_int(self):
        assert isinstance(now_ts(), int)

    def test_is_positive_and_recent(self):
        ts = now_ts()
        # 北京时间戳应该 > 2020-01-01 00:00:00+08
        assert ts > 1577836800 + 8 * 3600
        # 不应该超过当前 UTC 时间 + 1 天
        assert ts < int(_time.time()) + 9 * 3600

    def test_monotonically_increasing(self):
        a = now_ts()
        b = now_ts()
        assert b >= a

    def test_within_one_second_of_realtime(self):
        ts = now_ts()
        expected = int(_time.time() + 8 * 3600)
        assert abs(ts - expected) <= 1


class TestTsToDatetime:
    """ts_to_datetime() 转换测试"""

    def test_epoch_beijing(self):
        # 北京时间戳 0 = 1970-01-01 00:00:00+08（比 UTC epoch 早 8 小时）
        dt = ts_to_datetime(0)
        assert dt.year == 1970
        assert dt.month == 1
        assert dt.day == 1
        assert dt.hour == 0
        assert dt.minute == 0
        assert dt.second == 0
        assert dt.tzinfo is not None

    def test_known_timestamp(self):
        # 计算一个确定的时间戳：datetime → ts → datetime 往返验证
        from datetime import datetime as dt_cls
        ref = dt_cls(2026, 6, 17, 12, 0, 0, tzinfo=_BEIJING)
        ts = int(ref.timestamp() + 8 * 3600)
        result = ts_to_datetime(ts)
        assert result.year == 2026
        assert result.month == 6
        assert result.day == 17
        assert result.hour == 12
        assert result.minute == 0

    def test_roundtrip_with_now(self):
        """now_ts() → ts_to_datetime → back to ts 一致性"""
        ts = now_ts()
        dt = ts_to_datetime(ts)
        # 从 datetime 重建时间戳
        reconstructed = int(dt.timestamp() + 8 * 3600)
        assert reconstructed == ts


class TestValidateTs:
    """validate_ts() 校验测试"""

    def test_accepts_second_timestamp(self):
        assert validate_ts(1700000000) == 1700000000

    def test_rejects_millisecond_timestamp(self):
        with pytest.raises(ValueError, match="毫秒"):
            validate_ts(1700000000000)

    def test_rejects_microsecond_timestamp(self):
        with pytest.raises(ValueError, match="毫秒"):
            validate_ts(1700000000000000)

    def test_accepts_zero(self):
        assert validate_ts(0) == 0

    def test_accepts_2038_boundary(self):
        """2038-01-19 11:14:07+08, 32-bit signed max in Beijing"""
        assert validate_ts(2147483647 + 8 * 3600) == 2147483647 + 8 * 3600


class TestFmtTs:
    """fmt_ts() 格式化测试"""

    def test_format_format(self):
        result = fmt_ts(1781672400)
        assert "2026" in result
        assert "06" in result
        assert "17" in result

    def test_midnight(self):
        """午夜 00:00:00 — 用 datetime 精确构造时间戳"""
        midnight = datetime(2026, 6, 17, 0, 0, 0, tzinfo=_BEIJING)
        ts = int(midnight.timestamp() + 8 * 3600)
        result = fmt_ts(ts)
        assert "00:00:00" in result

    def test_no_timezone_suffix(self):
        """fmt_ts 是内部展示，不带时区后缀"""
        result = fmt_ts(now_ts())
        assert "+" not in result


class TestDayStr:
    """day_str() 测试"""

    def test_format(self):
        result = day_str(1781672400)
        assert result == "2026-06-17"

    def test_day_boundary_midnight(self):
        """午夜前后属于不同天"""
        midnight = datetime(2026, 6, 17, 0, 0, 0, tzinfo=_BEIJING)
        ts_midnight = int(midnight.timestamp() + 8 * 3600)
        just_before = day_str(ts_midnight - 1)  # 23:59:59 → 06-16
        just_after = day_str(ts_midnight)        # 00:00:00 → 06-17
        assert just_before == "2026-06-16"
        assert just_after == "2026-06-17"


class TestWeekStr:
    """week_str() 测试"""

    def test_format(self):
        result = week_str(1781672400)
        assert result.startswith("2026-W")
        assert int(result.split("W")[1]) >= 1
        assert int(result.split("W")[1]) <= 53


class TestUtcTsToBj:
    """utc_ts_to_bj() 转换测试"""

    def test_epoch_conversion(self):
        """Unix epoch 0 = 1970-01-01 08:00 北京"""
        assert utc_ts_to_bj(0) == 8 * 3600

    def test_standard_timestamp(self):
        result = utc_ts_to_bj(1700000000)
        assert result == 1700000000 + 8 * 3600

    def test_float_input(self):
        """float 输入也能处理"""
        result = utc_ts_to_bj(1700000000.5)
        assert result == int(1700000000.5 + 8 * 3600)

    def test_returns_int(self):
        assert isinstance(utc_ts_to_bj(100), int)


class TestBjTsToUtc:
    """bj_ts_to_utc() 转换测试"""

    def test_epoch_conversion(self):
        """北京时间 0 = Unix epoch -28800"""
        assert bj_ts_to_utc(0) == -8 * 3600

    def test_standard_timestamp(self):
        result = bj_ts_to_utc(1700000000 + 8 * 3600)
        assert result == 1700000000

    def test_float_input(self):
        result = bj_ts_to_utc(1700000000.5 + 8 * 3600)
        assert result == int(1700000000.5)

    def test_returns_int(self):
        assert isinstance(bj_ts_to_utc(100), int)


class TestRoundtrip:
    """往返转换一致性"""

    def test_utc_bj_utc_roundtrip(self):
        original = 1700000000
        bj = utc_ts_to_bj(original)
        back = bj_ts_to_utc(bj)
        assert back == original

    def test_bj_utc_bj_roundtrip(self):
        original = now_ts()
        utc = bj_ts_to_utc(original)
        back = utc_ts_to_bj(utc)
        assert back == original

    @pytest.mark.parametrize("ts", [
        0, 1, 1577836800, 1700000000, 2000000000, 2147483647,
    ])
    def test_various_timestamps(self, ts):
        """多种时间戳往返测试"""
        bj = utc_ts_to_bj(ts)
        assert bj_ts_to_utc(bj) == ts
