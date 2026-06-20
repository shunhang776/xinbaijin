# 白槿 v2 系统架构手册

> 写给新 Claude Code 会话的项目完整指南。读完就能正确启动和操作白槿。

---

## 一、项目定位

白槿是一个有长期记忆、情绪状态、自主意图的 AI 伴侣。技术栈：FastAPI + SQLite + FAISS + BGE-M3 + DeepSeek。

**不是 chatbot**。她有五层记忆引擎、呼吸状态、平行生活、欲望系统、联网感知。

---

## 二、启动服务

```bash
cd D:\xinbaijn && python main.py
```

服务监听 `http://127.0.0.1:8080`。引擎后台异步预热（~30s），启动日志会输出自检结果：

```
启动自检通过: LifeStream, 翻书
启动自检通过: DesireSystem, 5个欲望
启动自检通过: BreathingState, mood=0.70
```

**任何自检失败都是 ERROR 级别，必须修复后再操作。**

---

## 三、回复前 — 获取上下文

```bash
curl -s "http://127.0.0.1:8080/bridge/persona?text=用户的消息"
```

返回 JSON：`persona_prompt` 是完整的系统提示词，包含身份、记忆、情绪、感官、偏好、提醒。直接用它作为 system prompt。

---

## 四、回复后 — 同步记忆

### 基础写入
```bash
curl -s -X POST http://127.0.0.1:8080/bridge/update \
  -H "Content-Type: application/json" \
  -d '{"user_text":"用户消息","reply":"白槿回复"}'
```

### 带情绪快照
```bash
curl -s -X POST http://127.0.0.1:8080/bridge/update \
  -H "Content-Type: application/json" \
  -d '{"user_text":"消息","reply":"回复","emotion":"有点担心","emotion_event":"他加班到凌晨"}'
```

### 带精确时间戳
```bash
# ts 必须是北京时间秒级戳，毫秒戳直接报错
curl -s -X POST http://127.0.0.1:8080/bridge/update \
  -H "Content-Type: application/json" \
  -d '{"user_text":"消息","reply":"回复","ts":1718000000}'
```

### 带提醒
```bash
curl -s -X POST http://127.0.0.1:8080/bridge/update \
  -H "Content-Type: application/json" \
  -d '{"user_text":"明天去复查","reply":"记得空腹","emotion":"有点担心","reminder":"问他复查结果","reminder_at":1687382400}'
```

### 写入 session 事实（重要对话后）
```bash
curl -s -X POST "http://127.0.0.1:8080/bridge/session?ts=1718000000" -d "事实内容"
```

### 降级方案（服务不可用时）
```bash
python D:\xinbaijn\main.py update "用户消息" "回复" 2>NUL
python D:\xinbaijn\main.py update "用户消息" "回复" --emotion "有点心疼" 2>NUL
```

---

## 五、记忆查询

### CLI（不加载模型，需服务在线）
```bash
python cli.py ask "顺航喜欢喝什么"          # 记忆问答
python cli.py recent                         # 最近原始对话
python cli.py recent claude                  # 最近 session 事实
python cli.py recent transcript              # 最新 transcript 对话
python cli.py recent transcript --list       # 列出所有 transcript 文件
python cli.py analyze                        # 深度分析全部历史
```

### HTTP API
```bash
curl "http://127.0.0.1:8080/memory/ask?q=查询内容&token=baijin-memory-ask-2026&sender=cli"
curl "http://127.0.0.1:8080/memory/recent?source=claude&token=baijin-memory-ask-2026"
curl "http://127.0.0.1:8080/health"
curl "http://127.0.0.1:8080/emotion/timeline"
curl "http://127.0.0.1:8080/emotion/stats"
```

---

## 六、项目架构（53 个 .py 文件）

### 入口层（4 个）
| 文件 | 作用 | 触发 |
|------|------|------|
| `main.py` | FastAPI 主入口，QQ Bot Webhook + 桥接 API + CLI 模式 | 常驻 |
| `cli.py` | 轻量 HTTP CLI，不加载模型/引擎 | 手动 |
| `guard.py` | 进程守护，Cron 每 10 分钟检查 v2_ops.py | Cron |
| `v2_ops.py` | 运维守护（端口监控、健康检查、自动重启、备份） | 常驻子进程 |

### 应用层 `src/`（13 个）
| 文件 | 作用 | 触发时机 |
|------|------|----------|
| `auth.py` | QQ Bot ed25519 验签 | 每 QQ 消息 |
| `errors.py` | 全局异常处理，返回 ACK 阻止平台重试 | 异常时 |
| `health.py` | 健康检查面板（模块/DeepSeek/QQ 状态） | 启动注册 |
| `deepseek.py` | DeepSeek API 客户端（流式、常连、指数退避） | 每 LLM 调用 |
| `tools.py` | AI Native 工具定义（6 个）+ execute() 分发 | 每 LLM 工具调用 |
| `identity.py` | 加载 config/identity.txt 人设 | 启动时 |
| `activity.py` | 用户活跃时段学习 | 每消息 |
| `safety.py` | 四层联网安全过滤 | 联网搜索时 |
| `internet.py` | SerpApi 联网搜索 + 感受改写 | LLM 调用工具时 |
| `vision.py` | 图片理解（URL→视觉 API→描述） | QQ 消息含图片 |
| `easter_eggs.py` | 渐进式触发彩蛋 | 每消息（LLM 前） |
| `habits.py` | 长期习惯学习（LLM 模式识别） | 每周日凌晨 5 点 |
| `compressor.py` | LLM 记忆压缩（多轮→单条摘要） | 每天凌晨 4 点 |

### 记忆引擎 `memory_engine/`（29 个）— 五层架构

#### 第零层：基础设施（4 个）
| 文件 | 作用 |
|------|------|
| `config.py` | **全项目唯一配置源**（路径/维度/权重/阈值），18 个模块依赖 |
| `constants.py` | 全局常量（时间/情绪/提醒参数） |
| `utils.py` | 北京时间基础设施（now_ts / ts_to_datetime / validate_ts） |
| `db.py` | SQLite WAL 模式，线程安全，增量迁移系统 |

#### 第一层：五个索引（6 个）
| 文件 | 索引类型 | 模拟什么 |
|------|----------|----------|
| `index_time.py` | 时间轴二分检索 | "上周三发生了什么" |
| `index_vector.py` | FAISS HNSW 1024d 语义 | "和这个意思相关的" |
| `index_bm25.py` | 中文 2-3 gram BM25 | "包含某个关键词的" |
| `index_associative.py` | 关键词↔ID 双向映射 | "提到 A 就想到 B" |
| `category_store.py` | Markdown 知识库 | "我知道的常识" |
| `retrieve_core.py` | 五路并行检索调度器 | 融合+优先级+情绪关联 |

#### 第二层：写入管线（3 个）
| 文件 | 作用 |
|------|------|
| `write_pipeline.py` | 异步写入（同步写 JSONL→入队→后台处理索引），崩溃有快照恢复 |
| `resource_store.py` | 原始对话按天写 JSONL，唯一不进内存的层 |
| `emotion.py` | 情绪快照（A 级永久记忆） |
| `preferences.py` | 偏好提取（规则匹配→Markdown 去重写入） |

#### 第三层：检索管线（4 个）
| 文件 | 作用 |
|------|------|
| `retrieve.py` | 记忆检索+艾宾浩斯遗忘曲线+A 级豁免+模糊化 |
| `retrieve_core.py` | 四路并行检索核心（时间/向量/BM25/关联） |
| `memory_fidelity.py` | 艾宾浩斯遗忘模型（7 个衰减节点） |
| `recent_chat.py` | 短期对话缓存（取最近 N 轮） |
| `background.py` | 语义云生成器（FAISS→链式联想→竞争归一化→互抑制） |

#### 第四层：背景维护（2 个）
| 文件 | 作用 |
|------|------|
| `evolve.py` | 演化引擎（C 级过期清理/去重/升降级/备份），CPU 上限 30% |
| `cooccurrence.py` | 赫布共现图（边权日衰减 5%，睡眠沉淀剪弱边） |

#### 第五层：自治行为 — 拟人模块（8 个）
| 文件 | 模拟什么 | 触发频率 |
|------|----------|----------|
| `state.py` | 情绪节律（心情/精力/社交欲），昼夜周期，第三意识，共时检测 | **每秒** tick |
| `senses.py` | 身体感知（天气/光线/CPU 温度→身体隐喻），非数字描述 | **每 prompt** |
| `life_stream.py` | 平行生活（翻书/喝茶/听音乐），情绪联动选活动，联网丰富 | **每 30 分** tick |
| `desire_system.py` | 5 个欲望池（联络/分享/求知/创作/反思），时间差累积模型 | **每 30 分** tick |
| `idle_thoughts.py` | 内心独白（90% 瞬逝/9% 短期/1% 长期），三层记忆漏斗 | **每 30 分** tick |
| `imperfect_speech.py` | 口语瑕疵（口吃/停顿/省略），10% 概率 | **每 QQ 回复** |
| `reminder.py` | 提醒系统（到期推送） | 每 30 分检查 |
| `active_behavior.py` | 主动消息（已归档，被 DesireSystem 取代） | —— |

### 其他目录
| 目录 | 文件 | 作用 |
|------|------|------|
| `prompt/` | `builder.py` | **零规则 prompt 构建器**— 拼装 10 个数据源（身份+状态+感官+生活流+语义云+记忆+情绪+偏好+提醒+闲思） |
| `embedding/` | `__init__.py`, `config_embed.py`, `embedder.py` | BGE-M3 1024 维嵌入，路径自动检测 |
| `tools/` | `rebuild_faiss.py` | FAISS 全量重建（换模型后手动执行） |
| `scripts/` | `migrate_from_mem0.py` | Mem0 到新引擎的一次性迁移 |

---

## 七、关键数据流

### QQ 消息完整路径
```
QQ Webhook POST /bot
  → auth.py 验签
  → _handle_message()
    ├─ vision.py 图片提取
    ├─ activity.py 记录活跃
    ├─ state.ripple() 情绪涟漪（+0.03）
    ├─ state.check_coincidence() 共时检测
    ├─ life_stream.on_user_message() 打断活动
    └─ _process_message()
        ├─ easter_eggs.match() 彩蛋检查
        ├─ prompt/builder.py 拼装 10 源系统提示词
        │   ├─ identity + state.describe() + senses.snapshot()
        │   ├─ life_stream activity + background 语义云
        │   ├─ retrieve（记忆+遗忘+模糊）
        │   └─ preferences + reminders + idle_thoughts
        ├─ deepseek.generate_with_tools() LLM + 工具执行
        ├─ add_imperfections() 口语瑕疵
        └─ _post_process() → write_pipeline（全索引写入）
  → _send_qq_reply()
```

### 定时任务（每 30 分钟）
```
_scheduled_tasks()
  ├─ _try_consolidate()      每天 7 点 — LLM 日摘要
  ├─ _try_compress()         每天 4 点 — 记忆压缩
  ├─ _try_detect_habits()    每周日 5 点 — 习惯检测
  ├─ _try_reminders()        每 30 分 — 提醒检查
  ├─ _try_greet()            每 30 分 — 主动问候（用 DesireSystem）
  ├─ _try_idle_thought()     每 30 分 — 闲思 tick
  ├─ _try_life_stream()      每 30 分 — 生活流 tick
  ├─ _try_desire_system()    每 30 分 — 欲望池 tick
  └─ _shuffle_daily()        每日 0 点 — 语料洗牌
```

---

## 八、欲望系统详解

5 个欲望，按优先级排序（同 tick 只触发一个）：

| 优先级 | 欲望 | 速率 | 阈值 | 冷却 | 输出 |
|--------|------|------|------|------|------|
| 1 | `reflect_on` 反思欲 | 0.0015/s | 50 | 4h | 内部感悟（深夜+情绪低触发） |
| 2 | `share_obs` 分享欲 | 0.001/s | 55 | 3h | 对外消息（月 2 条配额） |
| 3 | `learn_about` 求知欲 | 0.0008/s | 65 | 6h | 启动 LifeStream 学习活动 |
| 4 | `create_something` 创作欲 | 0.0005/s | 60 | 8h | 写随笔入库 |
| 5 | `reach_out` 联络欲 | 0.002/s | 60 | 4h | 对外消息（周 2 条独立配额） |

时间差累积模型：`增量 = 真实流逝秒数 × 速率 + 瞬时事件加成`。重启有 12h 保护上限。

---

## 九、LifeStream 活动库

15 个基础活动，按情绪倾向分类：

| 活动 | 情绪倾向 | 可联网丰富 |
|------|----------|------------|
| 听音乐、闭目休息、望向窗外、喝水发呆、聆听风声 | 低落偏好 | —— |
| 翻书、泡茶、随手涂鸦、整理桌面、整理杂物 | 高涨偏好 | 翻书→冷知识、泡茶→茶文化 |
| 摆弄绿植、打理小盆栽、慢悠悠走动 | 中性 | —— |
| 翻看相册、翻看随笔 | 中性 | 有联网搜 |

情绪联动：`mood < 0.4` → 低落类权重 ×3；`mood > 0.7` → 高涨类权重 ×3。

---

## 十、常见操作

### 日常验证
```bash
python -m compileall D:\xinbaijn -q     # 全项目语法
cd D:\xinbaijn && python main.py        # 启动看自检日志
python cli.py recent claude             # 验证记忆正常
curl http://127.0.0.1:8080/health       # 健康检查
```

### 测试桥接
```bash
# 写入 session
curl -s -X POST http://127.0.0.1:8080/bridge/session -d "测试内容"
# 写入对话
curl -s -X POST http://127.0.0.1:8080/bridge/update \
  -H "Content-Type: application/json" \
  -d '{"user_text":"测试","reply":"收到"}'
# 查询记忆
curl "http://127.0.0.1:8080/memory/ask?q=测试&token=baijin-memory-ask-2026&sender=test"
```

### 重建 FAISS
```bash
python tools/rebuild_faiss.py
```

### 编码铁律（完整版见 `memory/baijin-code-rules.md`）
1. 每函数 ≤ 20 行
2. 零硬编码，全部进 config.py
3. 异常必须出声（`except: pass` 是重罪）
4. 变量先赋值后使用
5. 跨文件调方法，复制名字不凭记忆
6. 一次一个文件，写完等确认
7. 删改必清根，不留旧痕
