# 白槿 — 系统摘要

**日期**: 2026-06-09

---

## 一、系统概述

白槿是一个 26 岁的 AI 伴侣。系统由两大部分组成：

| 入口 | 载体 | 用户 |
|------|------|------|
| QQ Bot | FastAPI 服务 (port 8080) | 顺航（移动端） |
| Claude Code | 终端 CLI | 顺航（桌面端） |

两个入口通过共享记忆引擎实时同步，实现"不分彼此"的双端体验。

### 模型后端

- QQ 端：`deepseek-chat`（DeepSeek API 直连）
- Claude 端：`deepseek-v4-pro`

---

## 二、目录结构

```
D:\xinbaijn\
├── main.py                  FastAPI 入口 · QQ Bot Webhook · 消息流水线
├── bridge.py                Claude Code 桥接 CLI
├── guard.py                 守护的守护
├── v2_ops.py                运维守护（端口监控 + 自动重启 + 定时备份）
│
├── config\
│   └── config.yaml          全局配置
│
├── embedding\               BGE-M3 嵌入模型包（3 文件，86 行）
│   ├── __init__.py          预热 + 健康检查
│   ├── embedder.py          模型封装（Query/Doc 分锁）
│   └── config_embed.py      模型路径常量
│
├── memory_engine\           全本地记忆引擎（21 文件，~2,100 行）
│   ├── engine.py            引擎入口（单例）
│   ├── index_vector.py      FAISS 向量索引（1024 维）
│   ├── index_bm25.py        BM25 文本索引
│   ├── index_time.py        时间线索引
│   ├── index_associative.py 联想索引
│   ├── write_pipeline.py    异步写入流水线
│   ├── evolve.py            演化引擎（自动整合/压缩）
│   ├── retrieve.py          检索入口
│   ├── emotion.py           情绪模块
│   └── ...                  更多
│
├── src\                     应用层（30+ 文件）
│   ├── deepseek.py          DeepSeek API 客户端
│   ├── tools.py             模型工具定义 + 执行路由
│   ├── memory.py            记忆接口
│   ├── health.py            健康监控面板
│   ├── conversation.py      对话历史持久化
│   ├── identity.py          身份加载
│   ├── sleep.py             睡眠整合
│   └── ...
│
├── prompt\
│   └── builder.py           v2 统一 prompt 构建
│
├── data\                    运行时数据
│   ├── memory.db            SQLite 记忆数据库
│   ├── faiss.index          FAISS 向量索引
│   └── ...
│
├── logs\                    日志（10MB 轮转）
└── backup\                  每日自动备份
```

---

## 三、核心组件

### 3.1 BGE-M3 嵌入模型

- 模型：BAAI/bge-m3，1024 维向量
- 启动时阻塞预热，第一个请求零延迟
- Query/Doc 独立锁，检索写入互不阻塞

### 3.2 全本地记忆引擎

| 组件 | 技术 | 说明 |
|------|------|------|
| 向量索引 | FAISS HNSW | 1024 维语义检索 |
| 文本索引 | BM25 | 关键词全文检索 |
| 时间线 | JSONL | 时序存储 |
| 存储 | SQLite | 结构化数据 |
| 写入 | 异步流水线 | 不阻塞 |
| 演化 | 自动整合/压缩 | 定时任务 |

### 3.3 DeepSeek API

- 流式 + 工具调用
- 3 次指数退避重试
- 429/401 细化处理

---

## 四、消息处理流水线

```
用户消息
  → 彩蛋匹配
  → prompt 构建（记忆检索 + 人格 + 上下文）
  → DeepSeek 生成 + 工具调用
  → 记忆入库（remember_sync）
  → 对话记录（conversation.append）
  → 口语自然化（imperfect_speech）
  → QQ 异步发送
```

---

## 五、API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 模块状态 + DeepSeek/QQ 依赖检测 |
| GET/POST | `/bot` | QQ Bot Webhook |
| GET | `/bridge/persona` | Claude 端获取 persona prompt |
| POST | `/bridge/update` | Claude 端同步对话 |

---

## 六、设计原则

1. 每函数 ≤ 20 行
2. 先处理异常，不吞异常
3. 零硬编码
4. 模块解耦，单点故障不牵连全局
5. 删改必清根
6. 一次一个文件，写完等确认
