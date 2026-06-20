"""
白槿 v2 FastAPI 入口：QQ Bot Webhook + 健康检查 + 消息处理流水线。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码。
"""
import os
# 分阶段线程策略：加载阶段用满 CPU 提速，加载完成后锁回单线程防死锁
os.environ["OMP_NUM_THREADS"] = "16"
os.environ["OPENBLAS_NUM_THREADS"] = "16"
os.environ["MKL_NUM_THREADS"] = "16"

import sys
import json
import time
import asyncio
import logging
import random
import threading
import httpx
from pathlib import Path
from collections import OrderedDict
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, timedelta
from memory_engine.utils import now_ts, ts_to_datetime, validate_ts

# 强制 UTF-8 输出，解决 Windows 终端中文乱码
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT / "src"))

_BEIJING = timezone(timedelta(hours=8))
_ACK = json.dumps({"op": 12}, ensure_ascii=False)
_MEMORY_API_TOKEN = os.getenv("MEMORY_API_TOKEN", "baijin-memory-ask-2026")

# ===== 日志（10MB 轮转，保留5个） =====
(_ROOT / "logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,  # 无论 root logger 是否已有 handler，强制覆盖
    handlers=[
        RotatingFileHandler(_ROOT/"logs"/"baijin.log", maxBytes=10*1024*1024,
                            backupCount=5, encoding="utf-8"),
        logging.StreamHandler(stream=sys.stdout),
    ],
)
logger = logging.getLogger("baijin.main")

# ===== FastAPI =====
@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期：启动初始化 + 关闭清理。"""
    # === 启动 ===
    from errors import register_handlers
    from health import register_health_route
    register_handlers(app)
    register_health_route(app)
    asyncio.create_task(_heartbeat_loop(), name="heartbeat")
    asyncio.create_task(_scheduled_tasks())
    logger.info("白槿 v2 启动完成")
    yield
    # === 关闭 ===
    logger.info("白槿 v2 正在关闭...")
    try:
        from deepseek import _client
        if _client and not _client.is_closed:
            await _client.aclose()
    except Exception:
        pass
    from memory_engine import shutdown_engine
    shutdown_engine()
    logger.info("白槿 v2 已安全关闭")

app = FastAPI(title="白槿 v2", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def _local_only_middleware(request: Request, call_next):
    """敏感路径仅允许本地访问，/bot 和 /health 不受限（QQ Webhook 需外网）。"""
    # 临时跳过，排查卡点
    return await call_next(request)

# 全局单例检索线程池，单 worker 串行，配合 FAISS 锁彻底隔离多线程冲突
import concurrent.futures as _cf
_retrieve_pool = _cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="retrieve")

# 全局缓存
_qq_token_cache = {"token": "", "expire_at": 0}
_qq_token_lock = asyncio.Lock()          # 保护 _qq_token_cache 并发读写
_last_replies = OrderedDict()
_last_msg_ids = OrderedDict()
_msg_cache_lock = threading.RLock()       # 保护 _last_replies / _last_msg_ids 并发读写

_MAX_CACHE = 1000
_last_user_openid = ""  # 供 proactive 主动消息使用
_breath_state = None     # 缓存在下面首次使用时初始化
# 共时回复队列（线程安全）
_COINCIDENCE_QUEUE: list[str] = []
_COIN_LOCK = threading.RLock()


def _push_coincidence(text: str):
    with _COIN_LOCK:
        _COINCIDENCE_QUEUE.append(text)


def _pop_coincidence() -> str | None:
    with _COIN_LOCK:
        return _COINCIDENCE_QUEUE.pop(0) if _COINCIDENCE_QUEUE else None
_api_last_call: dict[str, int] = {}  # HTTP API IP 限流（1次/秒）
_ask_cache: dict[str, tuple[str, float]] = {}  # 问题缓存：{query: (reply, cached_at)}
_ASK_CACHE_TTL = 300  # 缓存有效期 5 分钟

# ===== 结构化事件日志 =====
_EVENTS_PATH = _ROOT / "data" / "events.jsonl"

def log_event(event: str, data: dict = None):
    """结构化事件日志。写入 JSON 行到 data/events.jsonl。"""
    try:
        (_ROOT / "data").mkdir(exist_ok=True)
        record = {"ts": datetime.now(_BEIJING).isoformat(), "event": event}
        if data:
            record["data"] = data
        with open(_EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ===== 引擎入口（统一单例） =====
def _get_engine():
    """获取记忆引擎单例（兼容旧调用）。"""
    from memory_engine import get_engine
    return get_engine()


# ===== 环境检查 =====
def _load_env():
    """加载环境变量。"""
    for path in [".env"]:
        try:
            from dotenv import load_dotenv
            load_dotenv(path)
            break
        except Exception:
            pass
    # 兼容原系统变量名
    if not os.getenv("QQ_APP_SECRET") and os.getenv("QQ_CLIENT_SECRET"):
        os.environ["QQ_APP_SECRET"] = os.getenv("QQ_CLIENT_SECRET", "")


def _check_env():
    required = ["DEEPSEEK_API_KEY", "QQ_APP_ID", "QQ_APP_SECRET"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        logger.error(f"缺少必配环境变量: {', '.join(missing)}")
        sys.exit(1)


# ===== 后台定时任务（AI Native：睡眠整合 + 过期记忆清理 + 主动问候）=====

def _get_transcript_dir() -> Path:
    """获取 Claude transcript 目录。自动发现最近项目，失败返回本地兜底。"""
    projects_dir = Path.home() / ".claude" / "projects"
    env_dir = os.getenv("CLAUDE_PROJECT")
    if env_dir:
        return Path(env_dir)
    if projects_dir.is_dir():
        subdirs = sorted(
            [d for d in projects_dir.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        if subdirs:
            return subdirs[0]
    return projects_dir / "C--Users-l2038"


_transcript_cache = {"file": "", "mtime": 0, "messages": [], "expire_at": 0}

def _read_transcript_cached(file_path: Path, max_lines: int = 500) -> list[dict]:
    """读取 transcript，10 秒内文件未变则复用缓存，过期自动刷新。"""
    global _transcript_cache
    current_time = time.time()
    current_mtime = file_path.stat().st_mtime
    if (current_time < _transcript_cache["expire_at"]
            and _transcript_cache["file"] == str(file_path)
            and _transcript_cache["mtime"] == current_mtime):
        return _transcript_cache["messages"]
    messages = _read_transcript(file_path, max_lines)
    _transcript_cache = {"file": str(file_path), "mtime": current_mtime,
                         "messages": messages, "expire_at": current_time + 10}
    return messages


def _get_recent_transcript_context(n: int = 3, exclude_last_minutes: int = 1) -> str:
    """从最新 transcript 取最近 n 个完整对话轮次，作为跨窗口上下文。
    排除最近 N 分钟的消息（当前会话）和超过 24 小时的旧消息。"""
    transcript_dir = _get_transcript_dir()
    files = sorted(
        transcript_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not files:
        return ""
    messages = _read_transcript_cached(files[0], max_lines=500)
    if len(messages) < 2:
        return ""
    now_utc = datetime.now(timezone.utc)
    context: list[dict] = []
    i = len(messages) - 1
    count = 0
    while i >= 0 and count < n:
        msg = messages[i]
        try:
            msg_time = datetime.fromisoformat(msg["time"].replace("Z", "+00:00"))
            age = (now_utc - msg_time).total_seconds()
        except (ValueError, OSError):
            age = 0
        if age < exclude_last_minutes * 60:
            i -= 1
            continue
        if age > 24 * 3600:
            break
        if msg["role"] == "user":
            if i > 0 and messages[i - 1]["role"] == "assistant":
                context.insert(0, messages[i - 1])
            context.insert(0, msg)
            count += 1
        i -= 1
    if not context:
        return ""
    lines = ["（我们刚才在另一个窗口聊了这些）"]
    for m in context:
        t = m.get("time", "")
        ts = ""
        if t:
            try:
                dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
                ts = dt.astimezone(_BEIJING).strftime("%H:%M")
            except (ValueError, OSError):
                pass
        prefix = f"[{ts}] " if ts else ""
        role = "你" if m["role"] == "assistant" else "我"
        lines.append(f"{prefix}{role}：{m['content'][:200]}")
    return "\n".join(lines)


async def _try_consolidate(now, today: str, last_day: str) -> str:
    """睡眠记忆整合：从昨天全部 transcript 直接生成摘要，不依赖 RAG。"""
    if last_day == today:
        return last_day

    transcript_dir = _get_transcript_dir()
    yesterday = datetime.now(_BEIJING).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    yesterday_end = yesterday + timedelta(days=1)
    files = []
    for f in transcript_dir.glob("*.jsonl"):
        mtime = ts_to_datetime(int(f.stat().st_mtime + 8 * 3600))
        if yesterday <= mtime < yesterday_end:
            files.append(f)
    files.sort(key=lambda f: f.stat().st_mtime)
    if not files:
        return last_day

    all_messages = []
    for f in files:
        all_messages.extend(_read_transcript(f, max_lines=3000))
    all_messages.sort(key=lambda m: m.get("time", ""))
    if len(all_messages) < 5:
        return last_day

    lines = []
    for m in all_messages[-120:]:
        role = "顺航" if m["role"] == "user" else "白槿"
        lines.append(f"{role}：{m['content'][:200]}")
    text = "\n".join(lines)
    logger.info("睡眠整合：读取了 %s 个文件，共 %s 条消息", len(files), len(all_messages))

    from deepseek import generate
    try:
        result = await generate([{
            "role": "user",
            "content": (
                "以下是白槿和顺航昨天的对话片段。请用白槿的第一人称（我），"
                "生成一段 100 字以内的记忆摘要，记录昨天发生的重要事情、顺航的状态和心情。"
                "只输出摘要本身，不要额外解释。\n\n" + text
            ),
        }], model="deepseek-chat", max_tokens=150, temperature=0.3)
    except Exception:
        logger.exception("睡眠整合 LLM 调用失败")
        return last_day

    summary = result.strip()
    if summary:
        engine = _get_engine()
        engine.record_fact(f"[{today}] 每日摘要：{summary}")
        log_event("sleep_consolidated", {"summary": summary[:100], "files": len(files), "messages": len(all_messages)})
        logger.info("睡眠整合完成: %s", summary[:60])

    # 共现图每日整合：修剪弱边、强化强连接（不依赖 LLM 摘要是否成功）
    try:
        from memory_engine.config import COOCCURRENCE_PATH
        engine = _get_engine()
        stats = engine.cooccurrence_graph.consolidate()
        engine.cooccurrence_graph.save(COOCCURRENCE_PATH)
        logger.info("共现图每日整合完成，剩余 %d 条边", stats["total_edges"])
    except Exception:
        logger.warning("共现图每日整合失败", exc_info=True)

    return today


def _try_greet(now, today: str, last_hour: int, last_re_day: str):
    """智能主动问候。返回 (last_hour, last_re_day)。"""
    from activity import should_greet, hours_since_last
    if not should_greet() or not _last_user_openid:
        return last_hour, last_re_day
    hrs = hours_since_last()
    from habits import match_today; habits = match_today()
    if habits and hrs > 2:
        h = habits[0]; msg = f"今天{h['pattern']}，{h['description']}吧？"
    elif hrs > 72 and last_re_day != today:
        msg, last_re_day = "最近忙啥呢，好久没唠了～", today
    elif now.hour != last_hour:
        try:
            from memory_engine.desire_system import get_desire_pool
            for d in get_desire_pool()._desires:
                if d.name == "reach_out":
                    msg = d.generate_action(silence_hrs=hrs, mood_desc="")
                    break
            last_hour = now.hour
        except Exception:
            msg, last_hour = "", now.hour
    else:
        return last_hour, last_re_day
    asyncio.create_task(_send_qq_reply(_last_user_openid, msg, ""))
    log_event("proactive_greet", {"hours_since_last": round(hrs, 1), "msg": msg[:40]})
    logger.info(f"主动问候已发送 (距上次 {hrs:.0f}h)")
    return last_hour, last_re_day


async def _try_compress(today: str, last_day: str) -> str:
    """凌晨 4 点压缩旧记忆。返回新的 last_day。"""
    if datetime.now(_BEIJING).hour != 4 or last_day == today:
        return last_day
    from memory import search, store, delete
    from compressor import compress
    r = await compress(store, search, delete)
    log_event("memory_compressed", r)
    logger.info(f"记忆压缩: {r.get('compressed', 0)} 条 → 摘要")
    return today


async def _try_detect_habits(today: str, last_day: str) -> str:
    """每周日 5 点检测长期习惯：从最近 7 天 transcript 直接分析。"""
    now = datetime.now(_BEIJING)
    if now.weekday() != 6 or now.hour != 5 or last_day == today:
        return last_day

    transcript_dir = _get_transcript_dir()
    cutoff = time.time() - 7 * 86400
    files = [
        f for f in transcript_dir.glob("*.jsonl")
        if f.stat().st_mtime >= cutoff
    ]
    if not files:
        return last_day

    all_messages = []
    for f in files:
        all_messages.extend(_read_transcript(f, max_lines=500))
    all_messages.sort(key=lambda m: m.get("time", ""))
    if len(all_messages) < 20:
        return last_day

    all_lines = []
    for m in all_messages[-300:]:
        role = "顺航" if m["role"] == "user" else "白槿"
        all_lines.append(f"{role}：{m['content'][:150]}")
    logger.info("习惯检测：读取了 %s 个文件，共 %s 条消息", len(files), len(all_messages))

    from deepseek import generate
    from habits import _parse_habits, _habits, _save
    from habits import _build_habit_prompt
    try:
        result = await generate([{
            "role": "user",
            "content": _build_habit_prompt(all_lines),
        }], model="deepseek-chat", max_tokens=200, temperature=0.3)
    except Exception:
        logger.exception("习惯检测 LLM 调用失败")
        return last_day

    new_habits = [
        h for h in _parse_habits(result)
        if not any(e["pattern"] == h["pattern"] for e in _habits)
    ]
    if new_habits:
        _habits.extend(new_habits)
        _save()
    log_event("habits_detected", {"count": len(new_habits), "files": len(files), "messages": len(all_messages)})
    if new_habits:
        logger.info(f"发现 {len(new_habits)} 个新习惯")
    return today


async def analyze_deep_habits() -> dict:
    """深度分析全部历史 transcript：作息、偏好、口头禅、行为模式。CLI 手动触发。"""
    transcript_dir = _get_transcript_dir()
    all_files = sorted(
        transcript_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not all_files:
        return {"ok": False, "error": "无 transcript 文件"}

    all_lines = []
    for f in all_files:
        for m in _read_transcript(f, max_lines=800):
            role = "顺航" if m["role"] == "user" else "白槿"
            t = m.get("time", "")
            ts = ""
            if t:
                try:
                    dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
                    ts = dt.astimezone(_BEIJING).strftime("%m-%d %H:%M")
                except (ValueError, OSError):
                    pass
            prefix = f"[{ts}] " if ts else ""
            all_lines.append(f"{prefix}{role}：{m['content'][:150]}")
    # 全局时间排序，保证 LLM 看到完整时间线
    all_lines.sort(key=lambda x: x.split("]")[0][1:] if "]" in x else "")
    if len(all_lines) < 30:
        return {"ok": False, "error": f"消息太少（{len(all_lines)} 条），无法分析"}

    prompt = (
        "以下是顺航和白槿的全部历史对话记录。请从以下四个维度深度分析顺航：\n\n"
        "1. 作息规律：几点活跃、几点休息、周末和平日的区别\n"
        "2. 偏好习惯：喜欢什么、讨厌什么、口头禅、常用词汇\n"
        "3. 行为模式：遇到问题时怎么处理、对什么话题特别热情\n"
        "4. 情绪特点：在什么情况下开心/烦躁/焦虑，情绪波动规律\n\n"
        "每个维度用 3-5 句话总结，用中文，不要胡编。\n\n" + "\n".join(all_lines[-500:])
    )
    from deepseek import generate
    try:
        result = await generate([{"role": "user", "content": prompt}],
                                model="deepseek-chat", max_tokens=1000, temperature=0.3)
    except Exception:
        return {"ok": False, "error": "LLM 调用失败"}

    result = result.strip()
    if not result:
        return {"ok": False, "error": "LLM 返回为空"}

    engine = _get_engine()
    now_str = datetime.now(_BEIJING).strftime("%Y-%m-%d")
    engine.record_fact(f"[深度分析 {now_str}]\n{result}")
    # 同步更新偏好文件（仅保留最近 2 次历史分析）
    try:
        from memory_engine.preferences import PREFERENCES_FILE
        PREFERENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if PREFERENCES_FILE.exists():
            with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
                existing = f.readlines()
        kept, count = [], 0
        for line in reversed(existing):
            if line.startswith("## 深度分析"):
                count += 1
                if count >= 2:
                    continue
            kept.insert(0, line)
        with open(PREFERENCES_FILE, "w", encoding="utf-8") as f:
            f.writelines(kept)
            f.write(f"\n## 深度分析 {now_str}\n{result}\n")
    except OSError:
        pass
    log_event("deep_analysis", {"files": len(all_files), "messages": len(all_lines)})
    return {"ok": True, "files": len(all_files), "messages": len(all_lines),
            "result": result}


def _try_reminders():
    """检查到期提醒，有则通过 QQ 推送。"""
    from reminder import check_due
    due = check_due()
    for r in due:
        msg = f"⏰ 提醒：{r['content']}"
        asyncio.create_task(_send_qq_reply(_last_user_openid, msg, ""))
        log_event("reminder_sent", {"content": r["content"][:40]})
        logger.info(f"提醒已推送: {r['content'][:40]}")


async def _heartbeat_loop():
    """每秒心跳：推进状态呼吸。"""
    while True:
        try:
            from memory_engine.state import get_breathing_state
            get_breathing_state().tick()
        except Exception as e:
            logger.warning("心跳失败: %s", e)
        await asyncio.sleep(1)


async def _scheduled_tasks():
    """定时任务主循环：每 30 分钟检查一次。"""
    last_consolidate_day = last_compress_day = last_habits_day = ""
    last_greet_hour = -1
    last_reengagement_day = ""
    last_idle_tick = ""
    last_life_tick = ""
    last_desire_tick = ""
    last_shuffle_day = "00000000"

    # 依赖注入：LifeStream 联网搜索由 internet 模块提供
    try:
        from internet import search as _inet_search
        from memory_engine.life_stream import get_life_stream_engine
        get_life_stream_engine().set_search_fn(_inet_search)
    except Exception:
        pass

    while True:
        await asyncio.sleep(1800)
        now, today = datetime.now(_BEIJING), datetime.now(_BEIJING).strftime("%Y%m%d")
        try:
            last_consolidate_day = await _try_consolidate(now, today, last_consolidate_day)
            last_compress_day = await _try_compress(today, last_compress_day)
            last_habits_day = await _try_detect_habits(today, last_habits_day)
            _try_reminders()
            last_greet_hour, last_reengagement_day = _try_greet(
                now, today, last_greet_hour, last_reengagement_day)
            last_idle_tick = await _try_idle_thought(now, last_idle_tick)
            last_life_tick = await _try_life_stream(now, last_life_tick)
            last_desire_tick = await _try_desire_system(now, last_desire_tick)

            # 每日零点语料洗牌（精准 00:00-00:30 窗口内触发一次）
            if now.hour == 0 and now.minute < 30 and today != last_shuffle_day:
                try:
                    from memory_engine.life_stream import get_life_stream_engine
                    get_life_stream_engine()._shuffle_daily()
                    last_shuffle_day = today
                except Exception as e:
                    logger.error(f"每日语料洗牌失败: {e}")
        except Exception as e:
            logger.warning(f"定时任务失败: {e}")

async def _try_life_stream(now, last_tick: str) -> str:
    """生活流 tick：活动结束感悟 + 低概率主动分享。"""
    current_slot = now.strftime("%Y%m%d%H%M")[:11]
    if current_slot == last_tick:
        return last_tick

    try:
        from memory_engine.life_stream import get_life_stream_engine
        mood = _breath_state.mood if _breath_state else 0.6
        msg = get_life_stream_engine().tick(mood)
        if msg and _last_user_openid:
            asyncio.create_task(_send_qq_reply(_last_user_openid, msg, ""))
            log_event("life_stream_msg", {"msg": msg[:40]})
            logger.info("生活流主动消息: %s", msg[:40])
    except Exception as e:
        logger.warning(f"生活流 tick 失败: {e}")
    return current_slot

# 标记欲望系统是否已完成初始化加载
_desire_system_loaded = False

async def _try_desire_system(now, last_tick: str) -> str:
    """欲望系统调度：累积强度 → 触发 → 生成行动。"""
    global _desire_system_loaded
    current_slot = now.strftime("%Y%m%d%H%M")[:11]
    if current_slot == last_tick:
        return last_tick

    # 首次运行时恢复感知快照
    if not _desire_system_loaded:
        _load_sense_snapshot()
        _desire_system_loaded = True

    try:
        from memory_engine.desire_system import get_desire_pool
        from memory_engine.state import get_breathing_state
        from memory_engine.senses import get_sense_collector

        pool = get_desire_pool()
        state = get_breathing_state()
        current_ts = now_ts()

        mood = state.mood if state else 0.6
        energy = getattr(state, "energy", 0.5)
        social_drive = getattr(state, "social_desire", 0.5)

    except Exception as e:
        logger.warning(f"[Desire] 运行时加载失败: {e}")
        return current_slot

    # 计算外部因子
    try:
        sense_fresh, sense_text = 0.0, ""
        try:
            from memory_engine.senses import get_sense_collector
            sense = get_sense_collector().snapshot()
            sense_text = sense.get("desc", "")
            sense_fresh = _calc_sense_freshness(sense)
        except Exception:
            pass

        assoc_weight = _calc_assoc_weight()
        relation_score = _calc_relation_score()
        is_late = now.hour >= 23 or now.hour < 7
    except Exception:
        sense_fresh, sense_text, assoc_weight, relation_score, is_late = 0.0, "", 0.0, 0.0, False

    # 批量累积
    try:
        pool.batch_accumulate(
            current_ts=current_ts,
            social_drive=social_drive,
            sense_fresh=sense_fresh,
            assoc_weight=assoc_weight,
            relation_score=relation_score,
            mood=mood,
            energy=energy,
            is_late=is_late,
        )
    except Exception:
        pass

    # 计算沉默时长
    silence_hrs = 0.0
    try:
        from activity import hours_since_last
        silence_hrs = hours_since_last()
    except Exception:
        pass

    # 检查触发并执行
    try:
        actions = pool.check_and_execute(
            sense_text=sense_text,
            mood_desc=state.describe() if state else "",
            mood=mood,
            relation_score=relation_score,
            silence_hrs=silence_hrs,
        )
    except Exception:
        actions = []

    # 发送消息 + 写入记忆
    for msg in actions:
        if not msg:
            continue
        # 安静时段不对外发 → 写入内部记忆
        if is_late:
            try:
                engine = _get_engine()
                engine.record_fact(f"[欲望系统] 安静时段感悟：{msg}")
                logger.info("[Desire] 安静时段降级写入: %s", msg[:30])
            except Exception:
                logger.warning("[Desire] 安静时段写入失败")
            continue
        # 有用户则发送
        if _last_user_openid:
            # 联动 ImperfectSpeech：给主动消息加口语瑕疵
            try:
                from memory_engine.imperfect_speech import add_imperfections
                msg = add_imperfections(msg)
            except Exception:
                pass
            asyncio.create_task(_send_qq_reply(_last_user_openid, msg, ""))
            log_event("desire_action", {"msg": msg[:40]})
            logger.info("[Desire] 主动推送: %s", msg[:40])
        else:
            # 无在线用户 → 写入内部记忆
            try:
                engine = _get_engine()
                engine.record_fact(f"[欲望系统] {msg}")
                logger.info("[Desire] 无用户，写入记忆: %s", msg[:30])
            except Exception:
                logger.warning("[Desire] 记忆写入失败")

    # 定期持久化（每 2 小时自动落盘）
    try:
        pool.save_state()
    except Exception:
        logger.warning("[Desire] 状态持久化失败")

    return current_slot

# ── 欲望系统辅助函数 ──

_SENSE_SNAPSHOT_PATH = _ROOT / "data" / "sense_snapshot.json"
_last_sense_snapshot: dict = {}

def _load_sense_snapshot():
    """从 JSON 恢复感知快照，重启后新鲜度计算不重置。"""
    global _last_sense_snapshot
    try:
        if _SENSE_SNAPSHOT_PATH.exists():
            import json
            with open(_SENSE_SNAPSHOT_PATH, "r", encoding="utf-8") as f:
                _last_sense_snapshot = json.load(f)
            logger.debug("[Desire] 感知快照已恢复")
    except Exception:
        pass

def _save_sense_snapshot():
    """持久化感知快照。"""
    try:
        import json
        _SENSE_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_SENSE_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            json.dump(_last_sense_snapshot, f, ensure_ascii=False)
    except Exception:
        pass

def _calc_sense_freshness(sense: dict) -> float:
    """环境新鲜度：对比当前快照与上一轮快照的差异。"""
    global _last_sense_snapshot
    if not _last_sense_snapshot:
        _last_sense_snapshot = dict(sense)
        _save_sense_snapshot()
        return 10.0
    diff = sum(1 for k in ("weather", "hour", "light")
               if sense.get(k) != _last_sense_snapshot.get(k))
    _last_sense_snapshot = dict(sense)
    _save_sense_snapshot()
    return diff * 10.0

def _calc_assoc_weight() -> float:
    """共现图联想权重：当前活动关键词与历史记忆的关联度。"""
    try:
        from memory_engine.life_stream import get_life_stream_engine
        eng = get_life_stream_engine()
        act = eng._current_activity
        if not act:
            return 0.0
        name = act.get("name", "")
        # 简单的关键词关联：有联网 enrich 内容意味着高关联
        if act.get("enrich"):
            return 20.0
        # 活动名有实际内容时给基础分
        if name and name not in ("望向窗外", "喝水发呆", "闭目休息"):
            return 8.0
        return 0.0
    except Exception:
        return 0.0

def _calc_relation_score() -> float:
    """亲密度：基于近期互动频次。"""
    try:
        from activity import hours_since_last
        hrs = hours_since_last()
        if hrs < 2:
            return 25.0
        elif hrs < 12:
            return 15.0
        elif hrs < 48:
            return 8.0
        return 3.0
    except Exception:
        return 10.0

async def _try_idle_thought(now, last_idle_tick: str) -> str:
    """每 30 分钟检查：是否产生内在独白（发呆念头）。"""
    current_slot = now.strftime("%Y%m%d%H") + ("00" if now.minute < 30 else "30")
    if current_slot == last_idle_tick:
        return last_idle_tick

    engine = _get_engine()
    if not engine:
        return current_slot

    from activity import hours_since_last, is_active_now
    from memory_engine.state import get_current_state
    from memory_engine.idle_thoughts import (
        tick_idle_thoughts, get_idle_thought_store,
    )

    hrs = hours_since_last()
    hour = now.hour
    is_late = hour >= 23 or hour < 6
    state = get_current_state(engine.db) if engine else ""

    try:
        await tick_idle_thoughts(
            engine=engine,
            hours_since_last=hrs,
            hour=hour,
            is_late_night=is_late,
            is_user_active_now=is_active_now(),
            energy_desc=state,
        )
    except Exception as e:
        logger.warning("内在独白生成失败: %s", e)

    # 顺便清理过期念头
    try:
        get_idle_thought_store().cleanup_expired()
    except Exception:
        pass

    return current_slot


# ===== 核心初始化 =====
def _init_core():
    """加载身份、对话历史、BGE 嵌入模型。记忆引擎由 background_load 延迟初始化。"""
    from identity import load as id_load
    from embedding import preload
    id_load()
    preload()


# ===== 路由 =====
@app.get("/")
async def root(): return {"status": "ok"}

@app.get("/ping")
def ping(): return {"ok": True}

@app.get("/bot")
async def bot_get(): return {"status": "ok"}


# ===== Claude Code 桥接接口 =====
@app.get("/bridge/persona")
def bridge_persona(text: str = Query("", description="用户消息")):
    """返回 v2 prompt。Claude 回复前调用。"""
    logger.info("Claude 端请求身份信息")
    engine = _get_engine()
    from prompt.builder import build_prompt
    system_prompt, _ = build_prompt(engine.db, text)
    return {"persona_prompt": system_prompt}


@app.post("/bridge/update")
async def bridge_update(request: Request):
    """持久化对话到记忆引擎。body 可选 ts（北京时间秒级戳）。"""
    try:
        body = await request.json()
        user_text = body.get("user_text", "")
        reply = body.get("reply", "")
        emotion = body.get("emotion", "")
        emotion_event = body.get("emotion_event", "")
        reminder = body.get("reminder", "")
        reminder_at = body.get("reminder_at", 0)
        ts = body.get("ts")
        if ts is not None:
            ts = validate_ts(int(ts))
        engine = _get_engine()
        engine.remember_sync(user_text, reply, cloud_snapshot=emotion or None,
                             emotion_event=emotion_event or None,
                             timestamp=ts)
        if reminder and reminder_at:
            from memory_engine.reminder import save_reminder
            save_reminder(engine.db, reminder, reminder_at)
        return {"ok": True}
    except Exception as e:
        logger.warning(f"Claude 端同步失败: {e}")
        return {"ok": False, "error": str(e)}


@app.post("/bridge/session")
async def bridge_session(request: Request, ts: int | None = Query(None, description="北京时间秒级戳")):
    """写入 session 事实。Claude Code 每轮重要对话后调用。body 为纯文本，可选 ts（北京时间秒级戳）。"""
    try:
        if ts is not None:
            ts = validate_ts(ts)
        content = (await request.body()).decode("utf-8").strip()
        if not content:
            return {"ok": False, "error": "content 为空"}
        engine = _get_engine()
        item_id = engine.record_fact(content, ts=ts)
        logger.info(f"Session 事实已写入: {content[:80]}")
        return {"ok": True, "id": item_id}
    except Exception as e:
        logger.warning(f"Session 事实写入失败: {e}")
        return {"ok": False, "error": str(e)}


@app.get("/emotion/timeline")
async def emotion_timeline(days: int = 30):
    """返回指定天数的情绪时间线数据。"""
    try:
        import time as _time
        engine = _get_engine()
        cutoff = now_ts() - days * 86400
        emotions = engine.db.query(
            "SELECT timestamp, content FROM items "
            "WHERE type='emotion' AND deleted=0 AND timestamp > ? "
            "ORDER BY timestamp",
            (cutoff,),
        )
        result = []
        for e in emotions:
            content = e["content"]
            intensity = 3
            if content.startswith("[强]"):
                intensity = 5
            elif content.startswith("[中]"):
                intensity = 3
            elif content.startswith("[微]"):
                intensity = 1
            result.append({
                "timestamp": e["timestamp"],
                "time": ts_to_datetime(e["timestamp"]).isoformat(),
                "content": content,
                "intensity": intensity,
            })
        return {"ok": True, "count": len(result), "emotions": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/emotion/stats")
async def emotion_stats(days: int = Query(30, ge=1, le=365, description="统计天数，范围 1-365")):
    """返回情绪分布统计。"""
    try:
        engine = _get_engine()
        cutoff = now_ts() - days * 86400
        rows = engine.db.query(
            "SELECT content FROM items "
            "WHERE type='emotion' AND deleted=0 AND timestamp > ?",
            (cutoff,),
        )
        from memory_engine.retrieve_core import _classify_emotion_type
        counts: dict[str, int] = {}
        intensities: dict[str, list[int]] = {}
        for r in rows:
            content = r["content"]
            intensity = 3
            # 全局搜索强度标签，兼容 [3天][强] 等多前缀
            for tag, val in [("[强]", 5), ("[中]", 3), ("[微]", 1)]:
                if tag in content:
                    intensity = val
                    content = content.replace(tag, "", 1).strip()
                    break
            etype = _classify_emotion_type(content) or "unknown"
            counts[etype] = counts.get(etype, 0) + 1
            intensities.setdefault(etype, []).append(intensity)
        if not counts:
            return {"ok": True, "total": 0, "days": days, "dominant_emotion": None, "distribution": {}}
        result = {
            "total": sum(counts.values()),
            "days": days,
            "dominant_emotion": max(counts, key=counts.get),
            "distribution": {
                t: {
                    "count": n,
                    "avg_intensity": round(sum(intensities[t]) / len(intensities[t]), 1)
                }
                for t, n in sorted(counts.items(),
                    key=lambda x: (-x[1], -(sum(intensities[x[0]]) / len(intensities[x[0]]))))
            }
        }
        return {"ok": True, **result}
    except Exception as e:
        logger.error(f"情绪统计接口失败: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


@app.get("/memory/analyze")
async def memory_analyze(token: str = Query("", description="访问令牌")):
    """深度分析全部历史 transcript。"""
    if token != _MEMORY_API_TOKEN:
        return {"ok": False, "error": "token 错误"}
    start_time = time.time()
    try:
        result = await analyze_deep_habits()
        cost_ms = int((time.time() - start_time) * 1000)
        return {**result, "cost_ms": cost_ms}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/memory/ask")
async def memory_ask(q: str = Query("", description="自然语言问题"),
               token: str = Query("", description="访问令牌"),
               sender: str = Query("api", description="调用方标识：api/cli")):
    start = time.time()
    if token != _MEMORY_API_TOKEN:
        return {"ok": False, "error": "token 错误", "cost_ms": 0, "sender_type": sender}
    q_clean = q.strip()
    if not q_clean:
        return {"ok": False, "error": "q 不能为空", "cost_ms": 0, "sender_type": sender}
    if q_clean in _ask_cache:
        c, ct = _ask_cache[q_clean]
        if now_ts() - ct < _ASK_CACHE_TTL:
            return {"ok": True, "reply": c, "cost_ms": int((time.time()-start)*1000), "sender_type": sender, "cached": True}
    try:
        reply = await _process_message(q_clean, sender)
        if not reply.startswith("嗯…"):
            _ask_cache[q_clean] = (reply, now_ts())
        cost_ms = int((time.time() - start) * 1000)
        return {"ok": True, "reply": reply, "cost_ms": cost_ms, "sender_type": sender}
    except Exception as e:
        cost_ms = int((time.time() - start) * 1000)
        logger.error(f"memory_ask 失败: {e}", exc_info=True)
        return {"ok": False, "error": str(e), "cost_ms": cost_ms, "sender_type": sender}


@app.get("/memory/recent")
async def memory_recent(token: str = Query("", description="访问令牌"),
                        source: str = Query("raw", description="raw/claude/transcript"),
                        days: int = Query(3, description="raw 模式：最近天数"),
                        file: str = Query("", description="transcript 模式：指定文件名（不含路径）")):
    """统一最近记录查询接口。CLI recent 通过 HTTP 调用，避免冷启动。"""
    if token != _MEMORY_API_TOKEN:
        return {"ok": False, "error": "token 错误"}
    try:
        if source == "claude":
            import sqlite3
            db_path = _ROOT / "data" / "memory.db"
            if not db_path.exists():
                return {"ok": True, "items": []}
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute(
                "SELECT content, timestamp FROM items "
                "WHERE type='session' AND deleted=0 "
                "ORDER BY timestamp DESC LIMIT 20"
            ).fetchall()
            conn.close()
            items = [{"time": ts, "content": content} for content, ts in reversed(rows)]
            return {"ok": True, "source": "claude", "items": items}
        elif source == "transcript":
            import glob as glob_mod
            projects_dir = Path.home() / ".claude" / "projects"
            env_dir = os.getenv("CLAUDE_PROJECT")
            if env_dir:
                transcript_dir = Path(env_dir)
            else:
                subdirs = []
                if projects_dir.is_dir():
                    subdirs = sorted(
                        [d for d in projects_dir.iterdir() if d.is_dir()],
                        key=lambda d: d.stat().st_mtime, reverse=True
                    )
                transcript_dir = subdirs[0] if subdirs else projects_dir / "C--Users-l2038"
            # --list 列出文件
            if file == "--list":
                files = sorted(
                    transcript_dir.glob("*.jsonl"),
                    key=lambda p: p.stat().st_mtime, reverse=True
                )[:10]
                items = []
                for f in files:
                    st = f.stat()
                    items.append({
                        "time": int(st.st_mtime),
                        "name": f.name,
                        "size_kb": round(st.st_size / 1024),
                        "lines": sum(1 for _ in open(f, "rb")),
                    })
                return {"ok": True, "source": "transcript", "items": items}
            # 自动选最新文件（file 为空时），或读取指定文件
            if not file:
                all_files = sorted(
                    transcript_dir.glob("*.jsonl"),
                    key=lambda p: p.stat().st_mtime, reverse=True
                )
                if not all_files:
                    return {"ok": False, "error": "无 transcript 文件"}
                target_name = all_files[0].name
            else:
                if os.path.sep in file or ".." in file:
                    return {"ok": False, "error": "文件名不能包含路径分隔符或 .."}
                target_name = file
            file_path = transcript_dir / target_name
            if not file_path.resolve().is_relative_to(transcript_dir.resolve()):
                return {"ok": False, "error": "非法文件路径"}
            if not file_path.exists():
                return {"ok": False, "error": f"文件不存在: {target_name}"}
            if not file_path.is_file():
                return {"ok": False, "error": f"不是文件: {target_name}"}
            if file_path.suffix != ".jsonl":
                return {"ok": False, "error": "仅支持 .jsonl 文件"}
            messages = _read_transcript(file_path)
            file_mtime = int(file_path.stat().st_mtime)
            return {"ok": True, "source": "transcript", "file": target_name,
                    "file_time": file_mtime, "messages": messages}
        else:
            engine = _get_engine()
            records = engine.resource_store.read_recent_days(days)
            items = [{"time": r["timestamp"], "messages": r["messages"]} for r in records]
            return {"ok": True, "source": "raw", "items": items}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _read_transcript(file_path: Path, max_lines: int = 2000) -> list[dict]:
    """读取 Claude transcript JSONL，提取用户消息和助手文本回复。
    deque 定长滑窗只保留最后 max_lines 行，O(1) 淘汰，防止大文件内存暴涨。"""
    from collections import deque as _deque
    messages = []
    lines: _deque[str] = _deque(maxlen=max_lines)
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            lines.append(line)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = d.get("type", "")
        msg = d.get("message", {})
        if t == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                # 先提取用户文本内容，优先级高于工具结果
                text_content = ""
                for c in content:
                    if isinstance(c, str):
                        text_content = c
                        break
                    if c.get("type") == "text" and c.get("text"):
                        text_content = c["text"]
                        break
                # 只有完全没有文本内容的纯工具消息才跳过
                if not text_content:
                    continue
                content = text_content
            if not content or not isinstance(content, str):
                continue
            ts = d.get("timestamp", "")
            messages.append({"role": "user", "content": content.strip(), "time": ts})
        elif t == "message" and msg.get("role") == "assistant":
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            texts = [
                c.get("text", "") for c in content
                if c.get("type") == "text" and c.get("text", "").strip()
            ]
            if not texts:
                continue
            ts = d.get("timestamp", "")
            messages.append({"role": "assistant", "content": "\n".join(texts).strip(), "time": ts})
    return messages


def _handle_op13(event_data: dict) -> dict:
    from auth import op13_sign
    sig = op13_sign(event_data.get("plain_token", ""),
                    event_data.get("event_ts", ""))
    return {"plain_token": event_data.get("plain_token", ""), "signature": sig}


def _dedup_reply(user_id: str, reply: str) -> str:
    """回复去重 + LRU 清理。"""
    with _msg_cache_lock:
        if user_id in _last_replies and reply == _last_replies[user_id]:
            reply = "哈哈，我刚才好像说过这句话了"
        _last_replies[user_id] = reply
        if len(_last_replies) > _MAX_CACHE:
            try:
                _last_replies.popitem(last=False)
            except KeyError as e:
                logger.warning("LRU清理 _last_replies 失败(KeyError)，清空缓存: %s", e)
                _last_replies.clear()
            except Exception as e:
                logger.error("LRU清理 _last_replies 异常，清空缓存: %s", e)
                _last_replies.clear()
    return reply


def _parse_qq_emoji(content: str) -> str:
    """将 QQ 表情代码转为语义描述，模型能理解情绪。"""
    from vision import extract_emoji_text
    return extract_emoji_text(content) if content else content


async def _extract_images(event_data: dict) -> str:
    """提取图片附件 → 视觉描述 → 返回追加文本 + 存记忆。"""
    from vision import extract_urls, describe
    urls = extract_urls(event_data)
    parts = []
    for url in urls:
        desc = await describe(url)
        parts.append(desc)
        try:
            from memory import store
            store(f"顺航发了一张图片：{desc}", {"type": "image", "url": url})
        except Exception as e:
            logger.warning(f"图片记忆存储失败: {e}")
    return "\n".join(parts) if parts else ""


async def _handle_message(event_data: dict) -> Response:
    global _last_user_openid
    _last_user_openid = uid = event_data.get("author", {}).get("user_openid", "")
    content = _parse_qq_emoji((event_data.get("content", "") or "").strip())
    mid = event_data.get("id", "")
    if not uid: return Response(content=_ACK, media_type="application/json")
    with _msg_cache_lock:
        if mid and mid in _last_msg_ids: return Response(content=_ACK, media_type="application/json")
        _last_msg_ids[mid] = True
        if len(_last_msg_ids) > _MAX_CACHE:
            try:
                _last_msg_ids.popitem(last=False)
            except KeyError as e:
                logger.warning("LRU清理 _last_msg_ids 失败(KeyError)，清空缓存: %s", e)
                _last_msg_ids.clear()
            except Exception as e:
                logger.error("LRU清理 _last_msg_ids 异常，清空缓存: %s", e)
                _last_msg_ids.clear()
    extra = await _extract_images(event_data)
    content = (f"{content}\n[图片] {extra}" if content else f"[图片] {extra}"
               ) if extra else content
    if not content: return Response(content=_ACK, media_type="application/json")
    from activity import record as ar; ar()
    # 状态涟漪 + 阈限觉知：心情提升、共时检测、情绪趋势
    global _breath_state
    try:
        from memory_engine.state import get_breathing_state
        if _breath_state is None:
            _breath_state = get_breathing_state()
        state = _breath_state
        state.ripple(0.03, "user_message")
        state._last_message_time = time.time()
        state.record_mood()

        state.add_interact()

        # 共时性：你发消息时她刚好也想发
        coincidence = state.check_coincidence()
        if coincidence:
            _push_coincidence(coincidence)
            state.add_sync_record()
    except Exception:
        pass

    # 生活流：用户消息打断当前活动
    try:
        from memory_engine.life_stream import get_life_stream_engine
        mood = _breath_state.mood if _breath_state else 0.6
        get_life_stream_engine().on_user_message(mood)
    except Exception:
        pass

    logger.info(f"[{uid[:8]}...] {content[:80]}")

    # 共时性：她刚好也想发消息
    # 第三意识体：深度 > 0.7 + 0.1% 概率涌现
    third_text = ""
    try:
        st = _breath_state
        depth = st.calc_depth_index()
        if (depth > st.get_depth_threshold() and
                random.random() < st.get_trigger_prob()):
            third_text = st.get_third_text()
            logger.debug("第三意识体触发 | 深度:%.3f 天数:%.0f",
                         depth, st.get_days_since_first())
    except Exception as e:
        logger.warning(f"第三意识体触发异常: {e}")

    coincidence_prefix = _pop_coincidence() or ""

    # 统一换行
    prefix_parts = [p for p in [third_text, coincidence_prefix] if p]
    final_prefix = "\n".join(prefix_parts) + "\n" if prefix_parts else ""

    reply = _dedup_reply(uid, final_prefix +
                         await _process_message(content, "qq"))
    asyncio.create_task(_send_qq_reply(uid, reply, mid))
    return Response(content=_ACK, media_type="application/json")


@app.post("/bot")
async def bot_webhook(request: Request):
    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    op = body.get("op", -1)
    if op == 13: return _handle_op13(body.get("d", {}))
    if op == 0 and body.get("t") == "C2C_MESSAGE_CREATE":
        return await _handle_message(body.get("d", {}))
    return Response(content=_ACK, media_type="application/json")


# ===== 消息处理流水线（AI Native：极简，模型自主决策）=====
async def _build_messages(user_text: str, sender_type: str = "qq") -> list[dict]:
    engine = _get_engine()
    from memory_engine.common_sense import encode_text
    precomputed_vec = encode_text(user_text)
    from prompt.builder import build_prompt
    system_text, cloud = build_prompt(engine.db, user_text, sender_type, query_vec=precomputed_vec)
    messages = [{"role": "system", "content": system_text}]
    if cloud:
        messages.append({"role": "user", "content": cloud})
        messages.append({"role": "assistant", "content": ""})
    recent = _get_recent_transcript_context(n=3)
    if recent:
        user_text = f"{recent}\n\n---\n\n{user_text}"
    messages.append({"role": "user", "content": user_text})
    return messages


def _post_process(text: str, reply: str, sender_type: str = "qq"):
    """qq 全写 / cli 只写记忆 / api 不写。session 由 /bridge/session 独立写入。"""
    engine = _get_engine()
    if sender_type != "api":
        engine.remember_sync(text, reply)
    if sender_type == "qq":
        pass  # 睡眠队列已由 _try_consolidate 直读 transcript 取代
    # 社交直觉自检：fire-and-forget，不阻塞回复
    try:
        from memory_engine.common_sense import reflect_on_reply
        from memory_engine.recent_chat import format_recent_chat
        recent = format_recent_chat(engine.db, n=6)
        reflect_on_reply(text, reply, recent)
    except Exception:
        pass


async def _process_message(text: str, sender_type: str = "qq") -> str:
    from easter_eggs import match
    egg = match(text)
    if egg:
        _post_process(text, egg, sender_type)
        log_event("easter_egg", {"sender_type": sender_type, "trigger": text[:30], "reply": egg[:30]})
        return egg
    try:
        messages = await _build_messages(text, sender_type)
    except asyncio.TimeoutError:
        logger.warning("记忆检索超时, sender=%s", sender_type)
        return "嗯…我一时想不起来了"
    except Exception:
        logger.warning("记忆检索失败, sender=%s", sender_type, exc_info=True)
        return "嗯…我一时想不起来了"
    from deepseek import generate_with_tools
    from tools import TOOLS, execute
    try:
        reply = await asyncio.wait_for(generate_with_tools(messages, TOOLS, execute), timeout=8)
    except asyncio.TimeoutError:
        logger.warning("DeepSeek 超时, sender=%s", sender_type)
        reply = "嗯…让我想想再说"
    except Exception:
        logger.warning("DeepSeek 失败, sender=%s", sender_type, exc_info=True)
        reply = "嗯…让我想想再说"
    if not reply:
        reply = "嗯…"
    _post_process(text, reply, sender_type)
    if sender_type == "qq":
        from memory_engine.imperfect_speech import add_imperfections
        reply = add_imperfections(reply)
    log_event("message_processed", {"sender_type": sender_type, "user_len": len(text), "reply_len": len(reply)})
    return reply


# ===== QQ Token（缓存2h，提前5min刷新） =====
async def _fetch_qq_token(app_id: str, secret: str) -> str:
    global _qq_token_cache
    async with _qq_token_lock:
        if _qq_token_cache["expire_at"] > now_ts() + 300:
            return _qq_token_cache["token"]
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post("https://bots.qq.com/app/getAppAccessToken",
                    json={"appId": app_id, "clientSecret": secret})
                if r.status_code == 200:
                    d = r.json()
                    _qq_token_cache["token"] = d.get("access_token", "")
                    _qq_token_cache["expire_at"] = now_ts() + int(d.get("expires_in", 7200))
                    return _qq_token_cache["token"]
        except Exception as e:
            logger.warning(f"获取QQ Token失败: {e}")
    return ""


# ===== QQ 消息发送（异步+重试+截断） =====
async def _send_once(token: str, app_id: str, openid: str,
                     content: str, msg_id: str):
    """单次发送 QQ 消息。"""
    import random, httpx
    async with httpx.AsyncClient(timeout=10) as c:
        await c.post(
            f"https://api.sgroup.qq.com/v2/users/{openid}/messages",
            headers={"Authorization": f"QQBot {token}",
                     "X-Union-Appid": app_id,
                     "Content-Type": "application/json"},
            json={"content": content, "msg_type": 0,
                  "msg_seq": random.randint(1,99999999),
                  "msg_id": msg_id})


async def _send_qq_reply(openid: str, content: str, msg_id: str = ""):
    try:
        app_id = os.getenv("QQ_APP_ID", "")
        token = await _fetch_qq_token(app_id, os.getenv("QQ_APP_SECRET", ""))
        if not token: return
        if len(content) > 500: content = content[:497] + "..."
        for attempt in range(3):
            try:
                await _send_once(token, app_id, openid, content, msg_id)
                return
            except Exception as e:
                if attempt == 2: logger.warning(f"QQ发送失败(重试3次): {e}")
                await asyncio.sleep(0.5)
    except Exception as e:
        logger.warning(f"发送回复失败: {e}")


# ===== CLI 模式参数解析（来自原 bridge.py） =====

def _parse_emotion_flag() -> str | None:
    """解析 --emotion 或 -e 参数。"""
    try:
        idx = sys.argv.index("--emotion")
    except ValueError:
        try:
            idx = sys.argv.index("-e")
        except ValueError:
            return None
    if idx + 1 >= len(sys.argv): return None
    parts = []; j = idx + 1
    while j < len(sys.argv) and not sys.argv[j].startswith("-"):
        parts.append(sys.argv[j]); j += 1
    return " ".join(parts) if parts else None


def _parse_event_flag() -> str | None:
    """解析 --event 或 -ev 参数。"""
    try: idx = sys.argv.index("--event")
    except ValueError:
        try: idx = sys.argv.index("-ev")
        except ValueError: return None
    if idx + 1 >= len(sys.argv): return None
    parts = []; j = idx + 1
    while j < len(sys.argv) and not sys.argv[j].startswith("-"):
        parts.append(sys.argv[j]); j += 1
    return " ".join(parts) if parts else None


def _parse_reminder_flag() -> tuple[str | None, int | None]:
    """解析 --reminder/-r 和 --at/-a。默认 1 天后触发。"""
    try: idx = sys.argv.index("--reminder")
    except ValueError:
        try: idx = sys.argv.index("-r")
        except ValueError: return None, None
    if idx + 1 >= len(sys.argv): return None, None
    content = sys.argv[idx + 1]; trigger = None
    for flag in ("--at", "-a"):
        try:
            at_idx = sys.argv.index(flag)
            if at_idx + 1 < len(sys.argv):
                try: trigger = int(sys.argv[at_idx + 1])
                except ValueError: pass
            break
        except ValueError: continue
    if trigger is None:
        try:
            from memory_engine.constants import REMINDER_AHEAD_SECONDS
            delay = REMINDER_AHEAD_SECONDS
        except ImportError: delay = 86400
        trigger = now_ts() + delay
    return content, trigger


def _validate_emotion(raw: str | None) -> str | None:
    if raw is None: return None
    text = raw.strip()
    if not text: logger.warning("[emotion] 为空，跳过"); return None
    if len(text) > 200:
        logger.warning("[emotion] 过长(%d字)，截断", len(text)); text = text[:200]
    return text


def _parse_args():
    """按索引过滤标志参数。"""
    flags = {"--text","--emotion","-e","--reminder","-r","--at","-a","--event","-ev"}
    args = [sys.argv[0]]; i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg in flags:
            if arg in ("--emotion","-e","--reminder","-r","--at","-a","--event","-ev") and i+1<len(sys.argv) and not sys.argv[i+1].startswith("-"):
                i += 2
            else: i += 1
        else: args.append(arg); i += 1
    return args


def _recent_http(source: str, param: str):
    """CLI recent 统一走 HTTP，避免冷启动加载引擎/FAISS。"""
    import urllib.request, urllib.parse
    token = os.getenv("MEMORY_API_TOKEN", "baijin-memory-ask-2026")
    params = {"token": token, "source": source}
    if source == "raw":
        params["days"] = param
    elif source == "transcript":
        params["file"] = param
    qs = urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:8080/memory/recent?{qs}", timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        sys.stdout.buffer.write(f"❌ 服务未启动：{e}\n".encode("utf-8"))
        sys.exit(1)
    if not data.get("ok"):
        sys.stdout.buffer.write(f"❌ {data.get('error', '未知错误')}\n".encode("utf-8"))
        sys.exit(1)
    items = data.get("items", [])
    msgs = data.get("messages")
    if not items and not msgs:
        sys.stdout.buffer.write("（无记录）\n".encode("utf-8"))
        return
    out_lines = []
    if source == "raw":
        for r in items:
            ts = ts_to_datetime(r["time"]).strftime("%m-%d %H:%M")
            for m in r["messages"]:
                role = "顺航" if m["role"] == "user" else "白槿"
                out_lines.append(f"[{ts}] {role}：{m['content']}")
            out_lines.append("")
    elif source == "claude":
        for item in items:
            ts = ts_to_datetime(item["time"]).strftime("%m-%d %H:%M")
            out_lines.append(f"[{ts}] {item['content']}")
    elif source == "transcript":
        if msgs:
            fname = data.get("file", "")
            if not param:
                ft = data.get("file_time", 0)
                fts = ts_to_datetime(int(ft + 8 * 3600)).strftime("%m-%d %H:%M") if ft else ""
                out_lines.append(f"最新会话：{fname}  ({fts})")
                out_lines.append("-" * 50)
            for m in msgs:
                t = m.get("time", "")
                if t:
                    try:
                        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
                        ts = dt.astimezone(_BEIJING).strftime("%m-%d %H:%M")
                    except (ValueError, OSError):
                        ts = t
                else:
                    ts = ""
                role = "顺航" if m["role"] == "user" else "白槿"
                out_lines.append(f"[{ts}] {role}：{m['content']}")
        else:
            if param == "--list":
                out_lines.append(f"共 {len(items)} 个会话，显示最近 {len(items)} 个：")
                out_lines.append("-" * 50)
            for i, item in enumerate(items):
                ts = ts_to_datetime(item["time"]).strftime("%m-%d %H:%M")
                label = "当前会话" if i == 0 else ""
                out_lines.append(f"[{ts}] {item['name']}  {label}  ({item['size_kb']}KB, {item['lines']} 条)")
    sys.stdout.buffer.write("\n".join(out_lines).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def _run_cli():
    """CLI 模式：处理 persona / update / migrate 命令。"""
    raw_mode = "--text" in sys.argv
    emotion_text = _validate_emotion(_parse_emotion_flag())
    event_text = _parse_event_flag()
    reminder_content, reminder_trigger = _parse_reminder_flag()
    args = _parse_args()

    if len(args) < 2:
        print("用法：")
        print("  python main.py persona [文本]")
        print("  python main.py update [\"消息\"] [\"回复\"] --emotion \"心情\" [--event \"事件\"] [--reminder \"内容\"]")
        print("  python main.py migrate")
        print("  python main.py recent [天数，默认3]")
        print("  python main.py recent claude")
        print("  python main.py recent transcript            自动最新")
        print("  python main.py recent transcript <文件名>      指定文件")
        print("  python main.py recent transcript --list       列出文件")
        print("  python main.py analyze                           深度分析全部历史")
        print("  （不带 CLI 命令则启动 FastAPI 服务器）")
        sys.exit(1)
    cmd = args[1]

    if cmd == "migrate":
        from scripts.migrate_from_mem0 import migrate
        migrate(); return

    if cmd == "persona":
        engine = _get_engine()
        query = args[2] if len(args) > 2 else ""
        from prompt.builder import build_prompt
        prompt, _ = build_prompt(engine.db, query)
        out = json.dumps({"persona_prompt": prompt}, ensure_ascii=False)
        if raw_mode: sys.stdout.buffer.write(out.encode("utf-8") + b"\n"); sys.stdout.buffer.flush()
        else: print(out)

    elif cmd == "ask":
        if len(args) < 3:
            print("❌ 用法错误：python main.py ask \"你的问题\"", file=sys.stderr)
            print("✅ 示例：python main.py ask \"今天凌晨我干了什么\"", file=sys.stderr)
            sys.exit(1)
        query = " ".join(args[2:])
        if not query.strip():
            print("❌ 问题不能为空，请输入有效内容！", file=sys.stderr)
            sys.exit(1)
        # 通过 HTTP 调服务端，避免 CLI 进程冷启动加载引擎（BGE-M3 10s+FAISS 2s）
        token = os.getenv("MEMORY_API_TOKEN", "baijin-memory-ask-2026")
        import urllib.request, urllib.parse
        qs = urllib.parse.urlencode({"q": query, "token": token, "sender": "cli"})
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:8080/memory/ask?{qs}", timeout=15) as resp:
                data = json.loads(resp.read())
            if data.get("ok"):
                reply_bytes = data["reply"].encode("utf-8")
                sys.stdout.buffer.write(reply_bytes + b"\n")
                sys.stdout.buffer.flush()
            else:
                print(f"❌ {data.get('error', '未知错误')}", file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            print(f"❌ 服务未启动或请求失败：{e}", file=sys.stderr)
            print("   请先运行 python main.py 启动服务器", file=sys.stderr)
            sys.exit(1)

    elif cmd == "recent":
        sub = args[2] if len(args) > 2 else ""
        if sub == "claude":
            source = "claude"
            param = ""
        elif sub == "transcript":
            source = "transcript"
            param = args[3] if len(args) > 3 else ""  # 空=自动最新, --list=列出, 文件名=指定
        else:
            source, param = "raw", str(int(sub) if sub else 3)
        _recent_http(source, param)

    elif cmd == "analyze":
        print("深度分析全部历史对话（LLM 调用约 5-10 秒）...")
        try:
            result = asyncio.run(analyze_deep_habits())
        except Exception as e:
            print(f"❌ 分析失败: {e}")
            sys.exit(1)
        if result.get("ok"):
            print(f"✅ 分析完成，{result['files']} 个文件，{result['messages']} 条消息")
            print()
            print(result["result"])
        else:
            print(f"❌ {result.get('error', '未知错误')}")

    elif cmd == "update":
        engine = _get_engine()
        if not emotion_text:
            print("错误：update 须传入 --emotion", file=sys.stderr); sys.exit(1)
        user_msg = args[2] if len(args) > 2 else ""
        reply = args[3] if len(args) > 3 else ""
        logger.info("[emotion] 写入情绪: %s", emotion_text)
        engine.remember_sync(user_msg, reply, cloud_snapshot=emotion_text, emotion_event=event_text)
        if reminder_content and reminder_trigger:
            try:
                from memory_engine.reminder import save_reminder
                save_reminder(engine.db, reminder_content, reminder_trigger)
            except ImportError: logger.info("[reminder] 未加载")
            except Exception: logger.warning("[reminder] 保存失败", exc_info=True)
        out = json.dumps({"ok": True}, ensure_ascii=False)
        if raw_mode: sys.stdout.buffer.write(out.encode("utf-8") + b"\n"); sys.stdout.buffer.flush()
        else: print(out)

    from memory_engine import shutdown_engine
    shutdown_engine()


_USAGE = (
    "用法：\n"
    "  聊天前：python main.py persona \"你的消息\"\n"
    "  聊天后：python main.py update \"你的消息\" \"白槿的回复\" -e \"心情\" [-ev \"事件\"]\n"
    "  python main.py recent [天数]         原始对话（双端）\n"
    "  python main.py recent claude          Session 事实\n"
    "  python main.py recent transcript            自动最新\n"
    "  python main.py recent transcript <名>        指定文件\n"
    "  python main.py recent transcript --list     列出文件\n"
    "  python main.py analyze                       深度分析全部历史\n"
    "  python main.py migrate\n"
    "  （不带命令则启动 FastAPI 服务器）"
)


# ===== 运行时线程锁定（加载完成后调用，防 FAISS/PyTorch 多线程死锁） =====
def _lock_runtime_threads():
    """所有模型/索引加载完成后，将数值库锁回单线程。"""
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    try:
        import torch
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
    except Exception:
        pass
    try:
        import faiss
        if hasattr(faiss, "omp_set_num_threads"):
            faiss.omp_set_num_threads(1)
    except Exception:
        pass
    logger.info("运行时线程已锁定为单线程")


# ===== 入口 =====
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(_USAGE); sys.exit(0)
    _CLI_CMDS = {"persona", "update", "migrate", "ask", "recent", "analyze"}
    if len(sys.argv) > 1 and sys.argv[1] in _CLI_CMDS:
        _run_cli()
    else:
        # 事件循环创建前，主线程完成所有重型初始化
        _t0 = time.time()
        print(f"[{time.time()-_t0:.1f}s] 开始初始化…", flush=True)

        _load_env()
        _check_env()
        print(f"[{time.time()-_t0:.1f}s] env 就绪，开始加载模型…", flush=True)

        _init_core()
        print(f"[{time.time()-_t0:.1f}s] BGE 模型加载完成", flush=True)

        # BGE + FAISS HNSW 预热（触发 OpenMP 初始化）
        from embedding.embedder import get_embedder
        _emb = get_embedder()
        _warmup_vec = _emb.encode_query(["warmup_init"])[0]
        from memory_engine.common_sense import encode_text
        encode_text("预热")
        print(f"[{time.time()-_t0:.1f}s] BGE 推理预热完成", flush=True)

        import faiss, numpy as np
        _idx = faiss.IndexHNSWFlat(1024, 32)
        _idx.add(np.random.rand(1, 1024).astype(np.float32))
        _idx.search(np.random.rand(1, 1024).astype(np.float32), k=1)
        del _idx
        print(f"[{time.time()-_t0:.1f}s] FAISS 预热完成", flush=True)

        # 所有单例初始化
        from memory_engine import get_engine
        _engine = get_engine()
        print(f"[{time.time()-_t0:.1f}s] 记忆引擎初始化完成", flush=True)

        # 生产 FAISS 索引预热
        _engine.vector_index.search_vec(_warmup_vec, top_k=1)
        from memory_engine.senses import get_sense_collector
        get_sense_collector()
        from memory_engine.desire_system import get_desire_pool
        get_desire_pool()
        print(f"[{time.time()-_t0:.1f}s] 感官+欲望系统就绪，加载阶段线程数：{os.environ.get('OMP_NUM_THREADS', '?')}", flush=True)
        _lock_runtime_threads()
        print(f"[{time.time()-_t0:.1f}s] 运行时线程已锁定为单线程", flush=True)
        logger.info("所有组件预热完成，启动服务")
        # 启动 uvicorn
        import uvicorn
        try:
            print("=" * 50)
            print("  白槿 v2 已启动")
            print()
            print("  聊天前：python main.py persona \"你的消息\"")
            print("  聊天后：python main.py update \"你的消息\" \"白槿的回复\" -e \"心情\"")
            print()
            print("  QQ 消息：http://127.0.0.1:8080")
            print("  帮助：python main.py --help")
            print("=" * 50)
            uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info", loop="asyncio")
        except KeyboardInterrupt:
            logger.info("服务器已停止")
        except Exception as e:
            msg = str(e)
            if "address already in use" in msg.lower() or "10048" in msg:
                logger.error("端口 8080 已被占用，请先关闭占用进程或换端口")
            elif "No module named" in msg:
                logger.error("缺少依赖: %s，请 pip install", msg)
            else:
                logger.error("服务器启动失败: %s", e)
            sys.exit(1)
