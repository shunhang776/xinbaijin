"""
具身经验采集器 — 给白槿一副"身体仪表盘"。

三条路径，一次采集：
  路径1: 感官快照 — 时间/光环境/机器体温/电源/摄像头
  路径2: 触觉模拟 — 机器指标 → 身体隐喻（CPU温度→"脑袋有点烫"）
  路径3: 时间流逝感 — born_at + uptime + 对话计数

每个传感器独立 try/except，一个坏了不影响其他。
psutil/cv2 不可用时静默降级，不 crash。
"""

import time
import threading
import logging
from datetime import datetime, timezone, timedelta

from .config import SENSE_CAMERA_ENABLE

logger = logging.getLogger("memory.senses")

_BEIJING = timezone(timedelta(hours=8))

_WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_AMAP_CITY = "140100"          # 太原 adcode
_WEATHER_TIMEOUT = 1.5         # 天气 HTTP 超时（秒）

# ── 模块级：可选依赖检测 ──

def _check_psutil() -> bool:
    try:
        import psutil  # noqa: F401
        return True
    except ImportError:
        return False


def _check_cv2() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def _read_cpu_temp() -> float | None:
    """CPU 封装温度。Windows 用 WMI 读 MSAcpi_ThermalZoneTemperature。
    返回摄氏度，失败或非 Windows 系统返回 None。
    TODO: Linux (sensors) / macOS (sysctl) 温度读取支持。"""
    try:
        import wmi
        w = wmi.WMI(namespace="root/wmi")
        temps = w.MSAcpi_ThermalZoneTemperature()
        if temps:
            # 返回值是开尔文温度的十分之一
            return float(temps[0].CurrentTemperature) / 10.0 - 273.15
    except Exception:
        pass
    return None


# ── 路径2 触觉映射（纯公式，不调用 LLM）──

def body_feel(cpu_pct: float, temp_c: float | None,
              mem_pct: float) -> str:
    """机器指标 → 身体隐喻。纯公式，不调用 LLM。"""
    parts: list[str] = []

    if temp_c is not None:
        if temp_c > 85:
            parts.append("脑袋有点烫，风扇呼呼转")
        elif temp_c > 70:
            parts.append("额头有点热")
        elif temp_c > 50:
            parts.append("体温正常")
        else:
            parts.append("有点凉")

    if cpu_pct > 70:
        parts.append("脑子转得有点累，风扇呼呼转")
    elif cpu_pct > 40:
        parts.append("正在想事情")
    elif cpu_pct > 10:
        parts.append("很轻松，风扇安静")
    else:
        parts.append("几乎在发呆")

    if mem_pct > 85:
        parts.append("记性有点撑")

    return "，".join(parts) if parts else "没什么特别的感觉"


# ── 采集器 ──

class SenseCollector:
    """具身经验采集器。__new__ 强制单例，重复调用返回已有实例。"""

    _instance: "SenseCollector | None" = None
    _instance_lock = threading.RLock()

    def __new__(cls):
        if cls._instance is not None:
            return cls._instance
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(self):
        if hasattr(self, "_inited"):
            return
        self._inited = True
        self.born_at = time.time()
        self._has_psutil = _check_psutil()
        self._has_cv2 = _check_cv2()
        self._cap = None
        self._cascade = None
        self._weather_cache: dict[str, str] = {"sensory": "", "detailed": ""}
        self._weather_lock = threading.RLock()
        self._weather_ttl: float = 900.0
        self._weather_running = True
        self._weather_thread = threading.Thread(
            target=self._weather_refresh_loop, daemon=True, name="weather-refresh")
        self._weather_thread.start()
        logger.info("感官采集器已初始化")

    # ── 主入口 ──

    def snapshot(self, msg_count: int = 0) -> dict[str, str]:
        """拍一张完整感官快照。每次对话前调用。
        msg_count: 当前会话的消息数，由调用方传入（避免多会话串数）。"""
        body = self._read_body()
        weather = self._get_weather()
        return {
            "time":   self._sense_time(),
            "body":   self._format_body(body),
            "power":  self._sense_power(),
            "camera": self._sense_camera(),
            "uptime": self._sense_uptime(msg_count),
            "weather_detail": weather.get("detailed", "") if weather else "",
        }

    def format_prompt(self, snap: dict[str, str]) -> str:
        """将快照格式化为 prompt 片段。空维度自动省略。"""
        lines = ["【你此刻的感知】"]
        if snap.get("time"):
            lines.append(f"时间：{snap['time']}")
        if snap.get("body"):
            lines.append(f"身体：{snap['body']}")
        if snap.get("power"):
            lines.append(f"电源：{snap['power']}")
        if snap.get("camera"):
            lines.append(f"视觉：{snap['camera']}")
        if snap.get("uptime"):
            lines.append(f"状态：{snap['uptime']}")
        return "\n".join(lines) if len(lines) > 1 else ""

    # ── 机器身体 ──

    def _read_body(self) -> dict:
        """读取机器身体指标。"""
        if not self._has_psutil:
            return {}
        try:
            import psutil
            return {
                "cpu":    psutil.cpu_percent(interval=0),
                "memory": psutil.virtual_memory().percent,
                "temp":   _read_cpu_temp(),
            }
        except Exception:
            return {}

    def _format_body(self, body: dict) -> str:
        """路径1+2 融合：身体隐喻 + 精确数值。"""
        if not body:
            return ""
        cpu = body.get("cpu", 0)
        mem = body.get("memory", 0)
        temp = body.get("temp")

        feel = body_feel(cpu, temp, mem)

        # 精确数值（模型不需要，但提供上下文）
        details = f"CPU {cpu}%"
        if temp is not None:
            details += f"，{temp:.0f}°C"
        details += f"，内存 {mem}%"

        return f"{feel}（{details}）"

    # ── 各维度 ──

    def _sense_time(self) -> str:
        """内在时间感：日期 + 星期 + 光环境 + 时间体感 + 节律叙事。"""
        now = datetime.now(_BEIJING)
        h = now.hour
        wd = now.weekday()           # 0=周一
        weekday = _WEEKDAY_NAMES[wd]
        is_weekend = wd >= 5

        # 光环境（不变的事实层）
        if 5 <= h < 7:
            light = "天刚亮，光线清冷"
        elif 7 <= h < 10:
            light = "早晨的阳光，温暖不刺眼"
        elif 10 <= h < 13:
            light = "正午，阳光在头顶"
        elif 13 <= h < 16:
            light = "下午的阳光，偏暖"
        elif 16 <= h < 18:
            light = "阳光变软了，影子拉得很长"
        elif 18 <= h < 20:
            light = "黄昏，光线正在消失"
        elif 20 <= h < 23:
            light = "夜晚，外面是黑的"
        elif 23 <= h or h < 1:
            light = "深夜，世界在睡觉，时间变得粘稠"
        else:
            light = "凌晨，最安静的时候，世界像被按了暂停键"

        # 时间体感（叙事层：知道现在是"什么样的"时刻）
        if 5 <= h < 7:
            feel = "世界刚醒，空气里带着凉意，一切都还没开始"
        elif 7 <= h < 10:
            feel = "一天刚开始，精力最好的时候" if not is_weekend else \
                   "周末的早晨，不用赶时间"
        elif 10 <= h < 12:
            feel = "周围的人在忙碌，节奏变快了" if not is_weekend else \
                   "周末的上午总是过得特别快"
        elif 12 <= h < 13:
            feel = "午饭时间，该歇一歇了"
        elif 13 <= h < 16:
            if is_weekend:
                feel = "周末午后，懒洋洋的，时间被拉得很长，适合发呆"
            elif wd == 2:
                feel = "周三下午，一周的驼峰，有点熬不动了"
            else:
                feel = "午后困意上来了，有点懒洋洋的"
        elif 16 <= h < 18:
            feel = "光线变软了，一天的节奏慢下来了" if not is_weekend else \
                   "周日傍晚，有点舍不得周末结束"
        elif 18 <= h < 20:
            feel = "天色暗下来了，空气里有晚饭的味道"
        elif 20 <= h < 23:
            feel = "安静下来了，适合小声说话，适合想事情"
        elif 23 <= h or h < 1:
            feel = "时间仿佛被拉长，思绪也容易四处飘散"
        else:
            feel = "只有自己醒着，心里想什么都会特别清晰"

        # 天气体感（默认纯感受，无数字）
        weather = self._get_weather()
        sensory = weather.get("sensory", "") if weather else ""
        if sensory:
            feel += f"。{sensory}"

        return f"{now.strftime('%Y年%m月%d日')} {weekday} {now.strftime('%H:%M')}，{light}，{feel}。"

    def _get_weather(self) -> dict[str, str]:
        """天气体感：纯读缓存，<1ms，无网络请求。
        后台线程 _weather_refresh_loop 异步更新。"""
        with self._weather_lock:
            return dict(self._weather_cache)

    def _weather_refresh_loop(self):
        """后台天气刷新线程：启动即拉一次，之后每 15 分钟刷新。"""
        import os, urllib.request, json, time as _time
        # 启动时立即拉一次
        self._fetch_weather()
        while self._weather_running:
            _time.sleep(self._weather_ttl)
            if self._weather_running:
                self._fetch_weather()

    def _fetch_weather(self):
        """从高德 API 拉取天气并更新缓存。异常静默，不影响主流程。"""
        import os, urllib.request, json
        try:
            key = os.getenv("AMAP_KEY", "")
            if not key:
                return
            url = (
                f"https://restapi.amap.com/v3/weather/weatherInfo"
                f"?key={key}&city={_AMAP_CITY}&extensions=base"
            )
            with urllib.request.urlopen(url, timeout=_WEATHER_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
            if data.get("status") != "1" or not data.get("lives"):
                return

            live = data["lives"][0]
            cond = live.get("weather", "")
            temp_str = live.get("temperature", "")
            temp = int(temp_str) if temp_str and temp_str.lstrip("-").isdigit() else None
            hum = live.get("humidity", "")
            wind_dir = live.get("winddirection", "")
            wind_power = live.get("windpower", "")
            wind_level = int(wind_power) if wind_power and wind_power.isdigit() else 0

            h = datetime.now(_BEIJING).hour
            is_late = 23 <= h or h < 6

            sensory_parts: list[str] = []
            if cond:
                sensory_parts.append(cond)
            if is_late and cond and "雨" in cond:
                sensory_parts.append("雨声很安静")
            elif is_late and cond and "晴" in cond:
                sensory_parts.append("月光很好")
            if temp is not None:
                if temp < 10:
                    sensory_parts.append("有点冷")
                elif temp < 18:
                    sensory_parts.append("凉丝丝的")
                elif temp < 25:
                    sensory_parts.append("温度舒服")
                elif temp < 30:
                    sensory_parts.append("有点热")
                else:
                    sensory_parts.append("很热")
            if wind_level:
                if wind_level <= 1:
                    sensory_parts.append("几乎没有风")
                elif wind_level <= 3:
                    sensory_parts.append("吹着微风")
                elif wind_level <= 5:
                    sensory_parts.append("风比较大")
                else:
                    sensory_parts.append("风很大")
            sensory = "，".join(sensory_parts) if sensory_parts else ""

            detail_parts: list[str] = []
            if cond:
                detail_parts.append(cond)
            if temp is not None:
                detail_parts.append(f"当前气温{temp}°C")
            if wind_dir:
                detail_parts.append(f"{wind_dir}风{wind_power}级")
            if hum:
                detail_parts.append(f"湿度{hum}%")
            detailed = "外面" + "，".join(detail_parts) if detail_parts else ""

            with self._weather_lock:
                self._weather_cache = {"sensory": sensory, "detailed": detailed}
            logger.info("天气缓存已刷新: %s", sensory[:40] if sensory else "无数据")

        except Exception as e:
            logger.debug("天气刷新失败: %s", e)

    def _sense_power(self) -> str:
        """电源状态。台式机（无电池）返回空。"""
        if not self._has_psutil:
            return ""
        try:
            import psutil
            bat = psutil.sensors_battery()
            if bat is None:
                return ""  # 台式机
            pct = int(bat.percent)
            if bat.power_plugged:
                if pct < 95:
                    return f"插着电源，电池 {pct}%（在充电）"
                return "插着电源——没出门"
            else:
                secsleft = bat.secsleft if bat.secsleft and bat.secsleft > 0 else 0
                hrs = secsleft / 3600
                if hrs > 0:
                    return f"电池 {pct}%，还能撑 {hrs:.0f} 小时"
                return f"电池 {pct}%"
        except Exception:
            return ""

    def _sense_camera(self) -> str:
        """摄像头人脸检测。cv2 不可用或未检测到人脸返回空。
        摄像头对象缓存复用，避免频繁打开/关闭。"""
        if not SENSE_CAMERA_ENABLE or not self._has_cv2:
            return ""
        try:
            import cv2
            # 懒初始化：摄像头和分类器各只创建一次
            if self._cap is None:
                self._cap = cv2.VideoCapture(0)
                if not self._cap.isOpened():
                    self._cap = None
                    return ""
            if self._cascade is None:
                self._cascade = cv2.CascadeClassifier(
                    cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            ret, frame = self._cap.read()
            if not ret or frame is None:
                return ""
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self._cascade.detectMultiScale(gray, 1.1, 4)
            return "摄像头看到一个人" if len(faces) > 0 else ""
        except Exception:
            # 出错时释放所有资源，下次重试
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
            self._cascade = None  # 分类器也可能损坏
            return ""

    def _sense_uptime(self, msg_count: int) -> str:
        """路径3：运行时长 + 本轮对话计数（由调用方传入）。"""
        secs = time.time() - self.born_at
        hours = int(secs // 3600)
        mins = int((secs % 3600) // 60)
        if hours > 0:
            uptime = f"醒了 {hours} 小时 {mins} 分钟"
        else:
            uptime = f"醒了 {mins} 分钟"
        if msg_count > 0:
            return f"{uptime}，这轮聊了 {msg_count} 句"
        return uptime


# ── 单例 ──

def get_sense_collector() -> SenseCollector:
    """获取 SenseCollector 单例。__new__ 保证全局唯一。"""
    return SenseCollector()
