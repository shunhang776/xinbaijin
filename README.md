# 白槿 v2 — 落地文档

> 2026-06-09，FastAPI + BGE-M3 + FAISS 全本地记忆引擎。

## 一、项目概述

白槿的数字生命系统，纯 Windows 原生部署。聊天走 DeepSeek API，语义嵌入（BGE-M3 1024维）+ 记忆检索（FAISS + BM25）100% 本地化。

**不依赖**：Docker、Ollama、Neo4j、云服务器

**仅依赖**：Python 3.12、FlagEmbedding、FAISS、FastAPI

## 二、目录结构

```
D:\xinbaijn\
├── main.py                    FastAPI 入口（QQ Bot Webhook + CLI）
├── README.md                  本文件
├── config\
│   └── config.yaml            全局配置
├── embedding\                 BGE-M3 嵌入模型包
│   ├── __init__.py            预热 + 健康检查
│   ├── embedder.py            模型封装（Query/Doc 分锁）
│   └── config_embed.py        模型路径 + 参数
├── memory_engine\             全本地记忆引擎
│   ├── engine.py              引擎入口（单例）
│   ├── index_vector.py        FAISS 向量索引
│   ├── index_text.py          BM25 文本索引
│   └── ...                    写入/演化/提醒/主动行为
├── src\                       应用层
│   ├── health.py              健康监控面板
│   ├── memory.py              记忆接口
│   ├── prompt\                Prompt 构建
│   └── ...                    对话/身份/情绪/工具
├── data\                      运行时数据
├── logs\                      日志（10MB 轮转）
└── backup\                    每日自动备份
```

## 三、架构图

```
用户消息（QQ / Claude Code）
          │
          ▼
    ┌─────────────┐
    │   main.py    │  FastAPI 入口
    │   /bot       │  QQ Webhook
    │   /bridge    │  Claude 桥接
    └──────┬───────┘
           │
    ┌──────┴──────────┐
    ▼                  ▼
┌─────────┐    ┌──────────────┐
│ DeepSeek│    │ memory_engine │
│  API    │    │ FAISS + BM25 │
└─────────┘    └──────┬───────┘
                      │
              ┌───────┴───────┐
              ▼               ▼
      ┌──────────────┐  ┌──────────┐
      │ embedding/   │  │ 时间线    │
      │ BGE-M3 1024维│  │ JSONL    │
      └──────────────┘  └──────────┘
```

## 四、核心模块

### 4.1 embedding/ — BGE-M3 嵌入模型包

| 函数 | 说明 |
|------|------|
| `preload()` | 启动时预热，加载 BGE-M3 到内存 |
| `is_ready()` | 健康检查，模型是否就绪 |
| `get_embedder()` | 获取单例（懒加载，线程安全） |

- 模型：BAAI/bge-m3，1024 维向量
- Query/Doc 独立锁，检索写入互不阻塞
- CPU 环境 fp16 关闭

### 4.2 memory_engine/ — 全本地记忆引擎

| 组件 | 说明 |
|------|------|
| FAISS 向量索引 | 1024 维语义检索 |
| BM25 文本索引 | 关键词检索 |
| 时间线 | JSONL 时序存储 |
| 写入线程 | 异步落盘 |
| 演化引擎 | 自动整合/压缩 |

### 4.3 main.py — FastAPI 入口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 模块状态 + 服务依赖 |
| `/bot` | GET/POST | QQ Bot Webhook |
| `/bridge/persona` | GET | Claude 端获取 persona prompt |
| `/bridge/update` | POST | Claude 端同步对话到记忆 |

## 五、启动流程

```
1. 加载环境变量 → 2. 环境检查
→ 3. 加载身份配置 → 4. 加载对话历史
→ 5. 预加载 BGE-M3 模型（阻塞，2-5s）
→ 6. 注册路由与异常处理
→ 7. 监听 8080 端口
→ 8. 后台异步预热记忆引擎
```

## 六、设计原则

- **每函数 ≤ 20 行**，超过就拆
- **先处理异常**，异常前置，不吞异常
- **零硬编码**：所有配置进常量或环境变量
- **模块解耦**：单点故障不牵连全局
- **删改必清根**：旧 import、旧路径、旧注释彻底消除
- **一次一个文件**：写完等确认再继续

## 七、CLI 命令

```bash
python main.py                  # 启动 FastAPI 服务器
python main.py persona "文本"   # 获取 persona prompt
python main.py update "消息" "回复" -e "心情"  # 同步对话
python main.py migrate          # 从旧 Mem0 迁移
```

## 八、生产环境（不动）

- `D:\xinbaijn\main.py` — QQ Bot Webhook（8080 端口）
- `D:\xinbaijn\v2_ops.py` — 运维守护
- `D:\xinbaijn\guard.py` — 守护的守护
- frp 隧道 + 云服务器 nginx — 外网通道
- DeepSeek API — 聊天生成
