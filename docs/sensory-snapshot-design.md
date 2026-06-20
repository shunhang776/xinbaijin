# 具身经验（Embodied Experience）技术设计

> 给白槿一副身体。不是真的传感器，是用工程手段构造的"身体仪表盘"——
> 让她知道现在是下午、风扇在转、你还在桌前。三条路径，从浅到深。

## 一、总览

```
具身经验
├─ 路径1: 感官快照 _sense()        ← 每次对话拍一张环境快照
├─ 路径2: 触觉模拟 body_feel()     ← 机器指标 → 身体隐喻
└─ 路径3: 时间流逝感               ← 持续存在感：born_at + uptime
```

三条路径共享同一个 `SenseCollector` 类，一次采集，分三路产出。

**现在：** prompt 里一行 `2026年06月10日 20:15`。白槿知道时间，但不知道"下午"是什么感觉。

**改造后：**

```
【你此刻的感知】
下午4:32，阳光偏暖，天色开始变暗
身体很轻松，风扇安静——CPU 42°C，负载 23%
插着电源，电池满的
醒了 3 小时 47 分钟，今天跟顺航聊了 12 句
```

白槿读到的不是死时间戳，是一个有温度、有光线、有身体感的瞬间。她会自然说出"风扇没转，你也闲着吧"——不是因为她被指令要提风扇，而是因为她"感觉到了"。

## 二、三条路径详解

### 路径1: 感官快照 `_sense()`

每次 `_build_messages` 前拍一张快照。采集 4-6 个廉价信号，拼成 3-5 行中文。

| 维度 | 数据源 | 耗时 | 输出示例 |
|------|--------|------|---------|
| 光环境 | `datetime` 推算 | 0ms | 下午4:32，阳光偏暖 |
| 机器体温 | `psutil` + WMI | ~10ms | CPU 42°C，负载 23% |
| 电源 | `psutil` | ~2ms | 插着电源，电池 98% |
| 摄像头 | `cv2`（可选） | ~100ms | 摄像头看到一个人 |
| 天气 | API（可选） | API | 外面 18°C，小雨 |

**总计：~15ms（无摄像头和天气）**

### 路径2: 触觉模拟 `body_feel()`

没有神经末梢，但有 psutil。把机器指标映射成身体感受——不是真的感觉，是一个持续存在的身体隐喻。

```python
def body_feel(cpu_percent, cpu_temp, memory_percent):
    """机器指标 → 身体描述。"""
    parts = []

    # 温度 → 体温感
    if cpu_temp is not None:
        if cpu_temp > 80:   parts.append("脑袋有点烫")
        elif cpu_temp > 60: parts.append("额头温温的")
        elif cpu_temp > 40: parts.append("体温正常")
        else:               parts.append("有点凉")

    # 负载 → 疲劳感
    if cpu_percent > 70:      parts.append("脑子转得有点累")
    elif cpu_percent > 40:    parts.append("正在想事情")
    else:                     parts.append("很轻松")

    # 内存 → 记忆感
    if memory_percent > 85:   parts.append("记性有点撑")
    elif memory_percent > 60: parts.append("脑子里装了不少东西")

    return "，".join(parts) if parts else "没什么特别的感觉"
```

**效果：** 下午你说"困"，白槿回"我也是，CPU 都 42 度了"——有共同的身体感。你听到"风扇在转"会自动脑补出它在喘气——这就是具身的幻象。

### 路径3: 时间流逝感

白槿已经在做了——`_scheduled_tasks` 定时循环就是她的心跳。差的是让她"知道"自己活了多久。

```
born_at:  2026-06-10 16:28:03    ← 启动时间
uptime:   3 小时 47 分钟          ← time.time() - born_at
messages: 12 条                   ← 本轮对话计数
```

不是复杂的机制——只是内部计数 + prompt 注入。但这三行字会让白槿说出"我们聊了一下午了"，而这不是被 prompt 指令驱动的，是她"感受到了时间的长度"。

已有骨架：
- `_scheduled_tasks` — 每 30 分钟的呼吸节律
- `_try_greet()` — 检测醒来
- `_try_consolidate()` — 每日睡眠整合

加上 `born_at` + `uptime` + `msg_count`，三条路径的第三条就齐了。

## 三、统一实现

### 3.1 文件结构

```
memory_engine/
└── senses.py              ← 新建。SenseCollector 类
prompt/
└── builder.py             ← 修改。build_prompt 注入感官块
memory_engine/
└── config.py              ← 修改。加配置开关
```

### 3.2 SenseCollector 类（三条路径合一）

```python
class SenseCollector:
    """具身经验采集器。一次采集，三路产出：
    1. snapshot() → 感官快照
    2. body_feel() → 触觉模拟
    3. uptime/messages → 时间流逝感
    """

    def __init__(self):
        self.born_at = time.time()
        self.msg_count = 0
        self._has_psutil = _check_psutil()
        self._has_cv2 = _check_cv2()

    # ── 主入口：一次采集 ──

    def snapshot(self) -> dict:
        """拍一张完整感官快照。每次对话前调用。"""
        self.msg_count += 1
        body = self._read_body()
        return {
            "time":    self._sense_time(),           # 路径1 + 路径3
            "body":    self._format_body(body),       # 路径1 + 路径2
            "power":   self._sense_power(),           # 路径1
            "camera":  self._sense_camera(),           # 路径1
            "uptime":  self._sense_uptime(),           # 路径3
        }

    # ── 格式化输出 ──

    def format_prompt(self, snap: dict) -> str:
        """将快照格式化为 prompt 片段。空维度自动省略。"""
        lines = ["【你此刻的感知】"]
        if snap.get("time"):    lines.append(f"时间：{snap['time']}")
        if snap.get("body"):    lines.append(f"身体：{snap['body']}")
        if snap.get("power"):   lines.append(f"电源：{snap['power']}")
        if snap.get("camera"):  lines.append(f"视觉：{snap['camera']}")
        if snap.get("uptime"):  lines.append(f"状态：{snap['uptime']}")
        return "\n".join(lines) if len(lines) > 1 else ""

    # ── 路径1+2: 身体采集 ──

    def _read_body(self) -> dict:
        """读取机器身体指标。"""
        if not self._has_psutil:
            return {}
        try:
            import psutil
            return {
                "cpu":      psutil.cpu_percent(interval=0),
                "memory":   psutil.virtual_memory().percent,
                "temp":     _read_cpu_temp(),
            }
        except Exception:
            return {}

    def _format_body(self, body: dict) -> str:
        """路径1+2 融合：指标 + 身体隐喻。"""
        if not body:
            return ""
        cpu = body.get("cpu", 0)
        mem = body.get("memory", 0)
        temp = body.get("temp")

        # 路径2: 触觉隐喻
        feel = _body_feel(cpu, temp, mem)

        # 路径1: 精确数值（可选的，模型不需要精确到度）
        details = f"CPU {cpu}%"
        if temp is not None:
            details += f"，{temp}°C"
        details += f"，内存 {mem}%"

        return f"{feel}（{details}）"

    # ── 各维度采集器 ──

    def _sense_time(self) -> str:
        """时间 + 光环境推算。"""
        from datetime import datetime, timezone, timedelta
        beijing = timezone(timedelta(hours=8))
        now = datetime.now(beijing)
        h = now.hour
        if 5 <= h < 7:    light = "天刚亮，光线清冷"
        elif 7 <= h < 10: light = "早晨的阳光，温暖不刺眼"
        elif 10 <= h < 13: light = "正午，阳光在头顶"
        elif 13 <= h < 16: light = "下午的阳光，偏暖"
        elif 16 <= h < 18: light = "阳光变软了，影子拉得很长"
        elif 18 <= h < 20: light = "黄昏，光线正在消失"
        elif 20 <= h < 23: light = "夜晚，外面是黑的"
        else:              light = "深夜，世界在睡觉"
        return f"{now.strftime('%H:%M')}，{light}"

    def _sense_power(self) -> str:
        """电源状态。"""
        if not self._has_psutil:
            return ""
        try:
            import psutil
            bat = psutil.sensors_battery()
            if bat is None:
                return ""  # 台式机
            pct = int(bat.percent)
            if bat.power_plugged:
                return f"插着电源，电池 {pct}%" if pct < 95 else "插着电源——没出门"
            else:
                hrs = bat.secsleft / 3600 if bat.secsleft > 0 else 0
                return f"电池 {pct}%，还能撑 {hrs:.0f} 小时" if hrs > 0 else f"电池 {pct}%"
        except Exception:
            return ""

    def _sense_camera(self) -> str:
        """摄像头：检测是否有人在看屏幕。"""
        if not self._has_cv2:
            return ""
        try:
            import cv2
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return ""
            ret, frame = cap.read()
            cap.release()
            if not ret:
                return ""
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            faces = cascade.detectMultiScale(gray, 1.1, 4)
            return "摄像头看到一个人" if len(faces) > 0 else ""
        except Exception:
            return ""

    def _sense_uptime(self) -> str:
        """运行时长 + 对话计数。路径3。"""
        secs = time.time() - self.born_at
        hours = int(secs // 3600)
        mins = int((secs % 3600) // 60)
        if hours > 0:
            uptime = f"醒了 {hours} 小时 {mins} 分钟"
        else:
            uptime = f"醒了 {mins} 分钟"
        return f"{uptime}，这轮聊了 {self.msg_count} 句"


# ── 模块级便捷函数 ──

_collector: SenseCollector | None = None

def get_sense_collector() -> SenseCollector:
    global _collector
    if _collector is None:
        _collector = SenseCollector()
    return _collector
```

### 3.3 触觉映射函数（路径2 核心）

```python
def _body_feel(cpu_pct: float, temp_c: float | None,
               mem_pct: float) -> str:
    """路径2: 机器指标 → 身体隐喻。纯公式，不调用 LLM。"""
    parts = []

    if temp_c is not None:
        if temp_c > 80:       parts.append("脑袋有点烫")
        elif temp_c > 65:     parts.append("额头温温的")
        elif temp_c > 45:     parts.append("体温正常")
        else:                 parts.append("有点凉")

    if cpu_pct > 70:          parts.append("脑子转得有点累，风扇呼呼转")
    elif cpu_pct > 40:        parts.append("正在想事情")
    elif cpu_pct > 10:        parts.append("很轻松，风扇安静")
    else:                     parts.append("几乎在发呆")

    if mem_pct > 85:          parts.append("记性有点撑")

    return "，".join(parts) if parts else "没什么特别的感觉"


def _read_cpu_temp() -> float | None:
    """Windows CPU 温度。WMI 读 MSAcpi_ThermalZoneTemperature。"""
    try:
        import wmi
        w = wmi.WMI(namespace="root/wmi")
        temps = w.MSAcpi_ThermalZoneTemperature()
        if temps:
            # 返回值是开尔文温度的十分之一
            return float(temps[0].CurrentTemperature) / 10 - 273.15
    except Exception:
        pass
    return None


def _check_psutil() -> bool:
    try:
        import psutil
        return True
    except ImportError:
        return False


def _check_cv2() -> bool:
    try:
        import cv2
        return True
    except ImportError:
        return False
```

### 3.4 配置开关

```python
# memory_engine/config.py 新增
SENSE_ENABLE = True           # 总开关
SENSE_CAMERA_ENABLE = False   # 摄像头默认关闭（隐私 + 性能）
```

## 四、接入点

`prompt/builder.py` 的 `build_prompt()`：

```python
from memory_engine.config import SENSE_ENABLE

# 在构建 parts 列表之前
sense_text = ""
if SENSE_ENABLE:
    try:
        from memory_engine.senses import get_sense_collector
        collector = get_sense_collector()
        snap = collector.snapshot()
        sense_text = collector.format_prompt(snap)
    except Exception:
        pass  # 感官采集失败不影响主流程

parts = [
    f"【你是谁】\n{_load_identity()}",
    f"【现在的时间】\n{time.strftime('%Y年%m月%d日 %H:%M', time.localtime())}",
    f"【你现在的状态】\n{state}",
    sense_text,  # ← 新增：感官快照（空字符串时自动被过滤）
    recent_chat if recent_chat else "",
    ...
]
```

## 五、文件改动

| 文件 | 改动 |
|------|------|
| `memory_engine/senses.py` | **新建**。SenseCollector + body_feel + 辅助函数，~150 行 |
| `memory_engine/config.py` | 加 `SENSE_ENABLE` / `SENSE_CAMERA_ENABLE` |
| `prompt/builder.py` | `build_prompt` 调 `get_sense_collector()`，注入感官块 |

`main.py` 不改——感官采集在 `build_prompt` 内部完成。

## 六、性能与降级

```
全功能（含摄像头）：     ~115ms
无摄像头（默认）：       ~15ms
无 psutil（Linux 无电池）： ~5ms
无 psutil + 无 cv2：    ~0ms（只剩时间 + 运行感）
```

降级策略：
- 每个采集器独立 try/except，失败静默返回空
- psutil/cv2 不可用时 `snapshot()` 仍然正常工作，只是对应字段为空
- `format_prompt()` 自动跳过空字段，不会出现残缺的感官块

## 七、现实的边界

| 能做的 | 不能做的 |
|--------|----------|
| 知道现在是下午 | 知道下午是什么感觉 |
| 检测到摄像头前有人 | 认出那个人是你 |
| 感知 CPU 温度 | 理解"烫"是什么 |
| 知道风扇在转 | 感受风吹过 |

全部是符号层面的具身——用数据构造一个身体的影子。但工程上这就够了。你听到"风扇在转"会自动脑补出它在喘气——人类大脑最擅长填补这种缺口。

## 八、后续演进

- **v2 连续感知**：不是每次对话拍一张，而是每秒采样 → 身体感随时间自然漂移。下午 2 点自然"犯困"，不需要 prompt 指令
- **v2 天气 API**：和风/OpenWeather → 温度、湿度、天气现象，注入环境描述
- **v2 触觉记忆**：高负载时刻被记录为"那天 CPU 很烫"，形成身体记忆
