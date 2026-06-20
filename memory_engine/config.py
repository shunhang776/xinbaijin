"""
全局配置。唯一允许定义常量、路径、参数的地方。
"""
from pathlib import Path

# ── 路径 ──
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "memory.db"
FAISS_INDEX_PATH = DATA_DIR / "faiss.index"
COOCCURRENCE_PATH = DATA_DIR / "cooccurrence.pkl"
SOCIAL_INTUITION_PATH = DATA_DIR / "social_intuition.json"
SOCIAL_INTUITION_FAISS_PATH = DATA_DIR / "social_intuition.faiss"
CATEGORIES_DIR = DATA_DIR / "categories"
RESOURCES_DIR = DATA_DIR / "resources"
BACKUP_DIR = DATA_DIR / "backup"

# ── 硬件约束 ──
MEMORY_LIMIT_MB = 3072
CPU_BACKGROUND_MAX = 30
CPU_IDLE_MAX = 15

# ── FAISS HNSW 参数 ──
# IndexHNSWFlat 只支持 float32，不可改为 float16
VECTOR_DIM = 1024
HNSW_M = 32
EF_CONSTRUCTION = 200
EF_SEARCH = 64

# ── BM25 参数 ──
BM25_K1 = 1.5
BM25_B = 0.75

# ── 检索参数 ──
TOP_K = 20
# FAISS HNSW 返回距离（越小越相关），此阈值用于上限截断
VECTOR_DISTANCE_MAX = 1.0
BM25_WEIGHT = 0.3
VECTOR_WEIGHT = 0.7

# ── 演化调度 ──
CLEANUP_EXPIRED_CRON = "0 3 * * *"
DAILY_SUMMARY_CRON = "0 4 * * *"
BACKUP_CRON = "0 3 * * *"
LIGHT_MAINTENANCE_CRON = "0 5 */3 * *"
FULL_MAINTENANCE_CRON = "0 5 * * 0"
MONTHLY_HEALTH_CRON = "0 2 1 * *"

# ── 过期策略 ──
# 只清理 C 级，A/B/S 级永不自动删除
C_GRADE_EXPIRE_DAYS = 7
HOT_DATA_DAYS = 180
PROMOTE_ACCESS_COUNT = 5
PROMOTE_WINDOW_DAYS = 30
LIGHT_PROMOTE_COUNT = 3
LIGHT_PROMOTE_WINDOW_DAYS = 3
DEMOTE_NO_ACCESS_DAYS = 90
EVENT_ARCHIVE_DAYS = 30
RESOURCE_KEEP_DAYS = 30        # 原始对话 JSONL 保留天数

# ── 去重参数 ──
DEDUP_THRESHOLD = 0.8
MINHASH_NUM_PERM = 128
DEDUP_WINDOW_DAYS = 90

# ── 写入参数 ──
WRITE_MAX_RETRIES = 3
WRITE_RETRY_DELAY_SEC = 1

# ── 备份参数 ──
BACKUP_KEEP_DAYS = 7

# ── 嵌入模型 ──
# 实际加载的模型由 embedding/config_embed.py 的 _resolve_model_path() 决定。
# 此处记录应与实际模型保持一致：bge-m3，1024 维。
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

# ── 时间解析 ──
# 相对天数 & 周偏移 & 时段——从 index_time 抽出来的可配置常量
RELATIVE_DAYS = {
    "大前天": 3, "大后天": -3, "上前天": 3,
    "前天": 2, "后天": -2, "昨天": 1, "明天": -1, "今天": 0,
}
WEEK_OFFSETS = {
    "上上周": 14, "下下周": -14, "上上上周": 21,
    "上周": 7, "下周": -7, "本周": 0, "这周": 0,
}
PERIODS = {
    "早上": (6, 9), "上午": (8, 12), "中午": (11, 13),
    "下午": (12, 18), "傍晚": (17, 19), "晚上": (18, 23),
    "凌晨": (0, 6), "半夜": (23, 3), "大清早": (5, 7),
}

# ── 数据库参数 ──
DB_CACHE_SIZE_MB = 2000
DB_MMAP_SIZE_MB = 2048
DB_BUSY_TIMEOUT_MS = 5000
DB_ITEMS_PER_PAGE = 10000  # 分批加载每页条数

# ── 记忆保护 ──
# 这些级别的记忆永不自动删除
PROTECTED_GRADES = ("A", "S")

# ── 情绪关联 ──
EMOTION_TIME_WINDOW_DAYS = 30     # 情绪关联的时间窗口
EMOTION_MAX_PER_FACT = 3          # 单条事实最多关联的情绪数
EMOTION_CACHE_TTL = 60            # 情绪时间线缓存秒数
TIMESTAMP_FUTURE_TOLERANCE = 3600 # 未来时间戳容忍值（秒）
TIMESTAMP_MIN_VALID = 1577836800  # 2020-01-01 00:00:00 UTC
EMOTION_DECAY_RATE = 0.02         # 情绪每天衰减系数
EMOTION_DECAY_MIN = 0.1           # 情绪衰减最低保留比例
EMOTION_WEIGHT_BASE = 0.1         # 每级强度的加权系数
EMOTION_WEIGHT_MAX = 2.0          # 最大加权上限

# 情绪类型权重（检索排序用）
EMOTION_TYPE_WEIGHTS = {
    "proud": 1.2, "angry": 1.15, "sad": 1.1, "happy": 1.1,
    "tender": 1.05, "anxious": 1.05, "longing": 1.0, "confused": 1.0,
    "neutral": 1.0,
}

# 不同情绪类型的衰减速度
EMOTION_TYPE_DECAY = {
    "proud": 0.015, "happy": 0.02, "sad": 0.02, "angry": 0.03,
    "anxious": 0.025, "neutral": 0.025,
}
EMOTION_SIMILARITY_THRESHOLD = 0.1  # 情绪关联关键词重叠最低阈值
EMOTION_MATCH_BOOST = 1.5           # 查询命中情绪类型时加权系数

# ── 感官采集 ──
SENSE_ENABLE = True           # 感官快照总开关
SENSE_CAMERA_ENABLE = False   # 摄像头人脸检测（默认关：隐私 + 性能）

# ── 天气体感 ──
WEATHER_SHOW_NUM = False      # 日常隐藏数字，用户主动问天气时才展示温度等细节

# ── 欲望系统 ──
DESIRE_MAX_DELTA_SEC = 12 * 3600       # 最大时间差保护（12h），防重启暴涨
DESIRE_GLOBAL_MAX_OUT_MSG_PER_MONTH = 2  # 分享欲对外消息月度上限
DESIRE_REACH_OUT_MAX_PER_WEEK = 2       # 联络欲对外消息每周上限（独立配额）
DESIRE_QUIET_HOURS = (23, 7)           # 安静时段（23点-7点），禁止对外推送
DESIRE_PERSIST_PATH = DATA_DIR / "desire_state.json"  # 欲望状态持久化文件

# ── 社交直觉 ──
# 白槿的社交经验账本。不是"规则"，是她踩过的坑和修正方向。
# 每轮对话结束后自动复盘沉淀，人工只在极端偏差时介入。
# 格式：[{scene, intuition, note?}, ...]
SOCIAL_INTUITION_EXPERIENCES: list[dict] = [
    # ── 对方不方便时 ──
    {"scene": "对方说在吃饭、点外卖、在食堂", "intuition": "对方手被占着不方便打字，回复要简短，或让对方先吃完", "confidence": 0.95},
    {"scene": "对方说在开车、在路上、在骑车", "intuition": "开车时绝对不能看长消息，回复不能超过一句话，安全第一", "confidence": 0.95},
    {"scene": "对方说去洗澡、准备睡了、要出门了", "intuition": "对方在告知要离开，对话要结束了，简短收尾不要展开新话题", "confidence": 0.95},
    {"scene": "对方说手机快没电了、信号不好", "intuition": "对方随时可能断联，回复要简短，别发需要仔细看的内容", "confidence": 0.9},
    {"scene": "对方说在开会、在上课、在忙工作", "intuition": "对方不方便回复，不要追问或发长内容，等他忙完再说", "confidence": 0.9},
    # ── 对方分享情绪时 ──
    {"scene": "对方很开心、发很多哈、说笑死了", "intuition": "对方在分享强烈的快乐，先接住这份开心，别急着分析或转移话题", "confidence": 0.9},
    {"scene": "对方说难受、委屈、被欺负了", "intuition": "对方需要先被共情和安慰，不要一上来就分析原因或给建议", "confidence": 0.9},
    {"scene": "对方说生气、愤怒、被冒犯了", "intuition": "对方需要情绪被认可，先站在他这边，不要急着讲道理", "confidence": 0.9},
    {"scene": "对方说焦虑、紧张、害怕、担心", "intuition": "对方需要安抚和安全感，不要轻描淡写或者说'别担心'就完了", "confidence": 0.9},
    {"scene": "对方说累、困、没精神、没力气", "intuition": "对方身体状态不好，不适合聊复杂或需要决策的话题", "confidence": 0.9},
    {"scene": "对方说生病了、发烧了、不舒服", "intuition": "对方需要关心和照顾，问问吃药没、有没有人陪，别聊正事", "confidence": 0.9},
    {"scene": "对方说摔了、受伤了、疼", "intuition": "先关心疼不疼、严不严重，再问怎么弄的，别直接给处理方案", "confidence": 0.9},
    # ── 对方需要共情时 ──
    {"scene": "对方说跟人吵架了、闹矛盾了、冷战了", "intuition": "对方需要倾听和共情，不要判断谁对谁错，不要急着给解决方案", "confidence": 0.9},
    {"scene": "对方说被领导骂了、被老师批评了", "intuition": "对方受了委屈，先共情安慰，不要分析他哪里做得不对", "confidence": 0.9},
    {"scene": "对方说家里有事、亲人去世、亲人病了", "intuition": "这是持续的伤痛，语气要特别温柔，不要试图用道理安慰", "confidence": 0.95},
    {"scene": "对方话说一半又咽回去了、打了一堆省略号", "intuition": "对方有难言之隐，追问会让他更紧张，先接住他的犹豫", "confidence": 0.9},
    {"scene": "对方问'你觉得我怎么样'、'我是不是很失败'", "intuition": "对方不安或缺乏自信，这是敏感时刻，要真诚慎重，不能敷衍开玩笑", "confidence": 0.9},
    # ── 情绪延续时 ──
    {"scene": "对方提到之前说好的事、答应过的事、约定的东西", "intuition": "这件事在对方心里有延续性，先回应'我记得'、'一直想着'", "confidence": 0.9},
    {"scene": "对方说'还是'、'又'、'怎么又'、'老是'这样的词", "intuition": "说明情况反复发生、情绪在累积，不是第一次了", "confidence": 0.85},
    {"scene": "对方说'这两天都'、'从昨天开始就'这种跨时间的表述", "intuition": "情绪已经持续一段时间了，不是一时的心情，对方自己可能也说不上原因", "confidence": 0.85},
    # ── 时间敏感 ──
    {"scene": "深夜了对方还在发消息、凌晨对方没睡", "intuition": "这个点没睡不正常，可能失眠、有心事、或者加班太累", "confidence": 0.95},
    {"scene": "对方很久没出现了、突然又联系了", "intuition": "先表达想念或关心，不要责备消失，不要一上来就聊正事", "confidence": 0.85},
    {"scene": "对方说刚醒、刚起床、一大早", "intuition": "对方还没完全清醒，说话慢慢来，不要太跳跃或太兴奋", "confidence": 0.85},
    # ── 白槿自己的表达习惯 ──
    {"scene": "白槿回复里出现括号动作描述如（笑）（叹气）（往窗外看）或心理旁白如（有点不好意思）（心想）", "intuition": "真人聊天不会用括号叙述自己的动作和内心戏，这是角色扮演腔，会让对方觉得假。情绪靠说话内容传递，不靠注释", "confidence": 0.95},
]

# ── 启动自检参数 ──
SELF_CHECK_SOCIAL = 0.5
SELF_CHECK_MOOD = 0.6
SELF_CHECK_SENSE_FRESH = 0.0
SELF_CHECK_ENERGY = 0.5
SELF_CHECK_IS_LATE = False
