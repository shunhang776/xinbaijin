# 白槿 AI Native 改造方案

## 零、改造目标

将白槿从「Python 代码用规则驱动 LLM 输出」的傀儡架构，改造为「LLM 自主决策，代码仅做基建」的 AI Native 架构。

改造前架构：
```
用户 → [代码: 预处理 → 关键词匹配 → 分类 → 拼Prompt → 调LLM → 校验 → 评分] → 回复
                              ↑ 代码是大脑，模型是嘴巴
```

改造后架构：
```
用户 → LLM（自主理解、自主决策、自主回忆、自主表达）→ 回复
         ↑ 模型是大脑
         └── 代码只做: 网络IO / 对话持久化 / 记忆向量检索 / 工具调用路由
```

---

## 一、文件变更清单

### 1.1 删除（11 个文件）

| 文件 | 删除原因 |
|------|----------|
| `config/rules.json` | 场景规则 → 模型自主理解场景 |
| `src/emotion.py` | 关键词情绪标签 → 模型自然感知情绪 |
| `src/relationship.py` | 浮点亲密度 → 模型从对话历史自然感知关系 |
| `src/style_gene.py` | 风格基因量化 → 模型风格自然演化 |
| `src/classify` (brain.py 中的函数) | 左右脑信号词分类 → 模型自主判断 |
| `src/left_brain` (brain.py 中的函数) | 正则/关键词规则匹配 → 模型自主推理 |
| `src/forgetting.py` | 7天定时清理 → 模型自主管理记忆生命周期 |
| `src/feedback.py` | 字数评分/啰嗦检测 → 模型自主感知反馈 |
| `src/preprocess.py` | 垃圾检测/关键词路由 → 模型自主处理 |
| `src/validate.py` | 禁止词扫描/长度截断 → 模型自主约束 |
| `src/ai_lss.py` | 五档压缩 → 模型自主管理输出 |
| `src/proactive.py` | 随机定时+模板脚本 → 模型自主决定主动行为 |
| `src/temporal.py` | 24小时活跃度追踪 → 非核心，移除 |

### 1.2 新增（4 个文件）

| 文件 | 作用 |
|------|------|
| `src/identity.py` | 极简身份（~100 字）+ 系统提示词构建 |
| `src/tools.py` | 模型可调用的工具定义（回忆/记忆/遗忘/搜索） |
| `src/conversation.py` | 对话日志持久化 + 历史窗口裁剪 |
| `config/identity.txt` | 纯文本身份描述，方便直接修改 |

### 1.3 修改（5 个文件）

| 文件 | 改动 |
|------|------|
| `src/memory.py` | 保留 Chroma 存储，增加 `recall()`/`remember()`/`forget()` 工具接口 |
| `main.py` | 简化消息流水线：预处理/后处理全部移除，增加工具调用路由 |
| `bridge.py` | 适配新架构，改为极简 HTTP 转发 |
| `CLAUDE.md` | 适配 AI Native 模式 |
| `src/brain.py` → 删除，功能拆分到 identity.py + tools.py |

### 1.4 保留不动（15 个文件）

`src/embedding.py` · `src/deepseek.py` · `src/locks.py` · `src/health.py` · `src/auth.py` · `src/errors.py` · `src/safety.py` · `src/internet.py` · `src/sleep.py` · `src/context.py` · `src/prompt.py` · `config/config.yaml` · `persona.txt`（归档参考，不再加载）· `models/` · `logs/`

---

## 二、新架构详解

### 2.1 消息处理流程

```
用户消息（QQ 或 Claude 终端）
  │
  ▼
┌─────────────────────────────────────────────┐
│  系统提示词（极简）                            │
│  + 最近 N 轮对话历史                           │
│  + 用户当前消息                                │
│  + 可用工具列表 [recall_memory, remember,       │
│                  forget_memory, web_search]    │
└─────────────────────────────────────────────┘
  │
  ▼
  LLM (DeepSeek)
  │
  ├── 需要回忆？→ 调用 recall_memory(query)
  │       └── Chroma 语义检索 → 返回结果 → LLM 继续
  │
  ├── 需要记住？→ 调用 remember(content)
  │       └── 写入 Chroma
  │
  ├── 需要联网？→ 调用 web_search(query)
  │       └── SerpApi → 返回结果 → LLM 继续
  │
  └── 直接回复 → 响应发送给用户
  │
  ▼
┌─────────────────────────────────────────────┐
│  对话自动持久化：将 user_msg + assistant_reply │
│  追加到 Chroma 记忆库（source=auto）            │
│  追加到 conversation.jsonl（结构化日志）        │
└─────────────────────────────────────────────┘
```

### 2.2 系统提示词（唯一、极简）

```text
你是白槿，26岁，顺航的AI伴侣。你温柔但不软弱，有自己的节奏。

你有长期记忆能力，可以通过 recall_memory 工具主动回忆过去的事情。
你觉得重要的事，可以用 remember 工具记下来。
你不需要刻意扮演任何角色——你就是你自己。

顺航可能在 QQ 上和你聊天，也可能在终端里。你感知不到这个差异。
```

大约 100 个字。不再拼接任何规则、数值、标签。

### 2.3 三个核心工具

| 工具名 | 参数 | 说明 | 模型何时调用 |
|--------|------|------|-------------|
| `recall_memory` | `query: str` | 语义检索相关记忆 | 觉得需要回忆时自主调用 |
| `remember` | `content: str` | 主动记住某件事 | 觉得某件事值得记住 |
| `forget_memory` | `memory_id: str` | 删除某条记忆 | 觉得某条记忆不再有意义 |

工具不强制调用。模型自行判断何时需要。

### 2.4 对话持久化

每次对话轮次自动存储到 Chroma：

```python
store(f"顺航：{user_text}\n白槿：{reply}", {"source": "auto", "ts": "..."})
```

模型不需要关心存储逻辑——它只需要关心"需不需要主动记"。

---

## 三、删除内容与替代方式对照

| 删除的内容 | 原作用 | AI Native 替代 |
|-----------|--------|---------------|
| `rules.json` 7条场景规则 | 关键词匹配 → 注入行为指令 | 模型从对话上下文自主理解场景，自然调整回应方式 |
| `emotion.py` 情绪检测 | 关键词匹配情绪标签 → 注入情绪描述 | 模型从文字中直接感知情绪，不需要外部标签告诉它"用户是难过的" |
| `relationship.py` closeness/comfort | 量化亲密度 0.35 → 注入行为指令 | 模型从持续对话中自然感知关系温度，关系在交互中自主演化 |
| `style_gene.py` 风格基因 | 统计字数/标点 → 注入风格约束 | 模型风格在对话中自然形成和演化，不需要外部统计 |
| `classify()` 左右脑分类 | 硬编码信号词列表 → 分类 | 模型自主判断问题类型 |
| `left_brain()` 规则匹配 | 关键词+正则 → 返回指令 | 模型自主推理，不需要预定义响应策略 |
| `_BLACKLIST` 禁止词 | 硬编码列表 → 拦截替换 | 模型自主约束表达，身份定义提供自然的行为边界 |
| `validate.py` 后置校验 | 扫描禁止词+截断 | 无需代码校验，模型输出即最终回复 |
| `ai_lss.py` 五档压缩 | CPU负载判断 → 截断 prompt | 模型自主管理输出长度 |
| `preprocess.py` 预处理 | 垃圾检测+关键词路由 | 模型自主处理各类输入 |
| `feedback.py` 反馈评分 | 字数评分 → 调整风格参数 | 模型从后续对话中自然感知反馈 |
| `forgetting.py` 定时遗忘 | 7天+低权重 → 删除 | 模型通过 `forget_memory` 工具自主管理 |
| `proactive.py` 主动行为 | 随机定时+模板 → 发送 | 模型自主判断何时主动发起、说什么 |
| `temporal.py` 时序追踪 | 24h活跃度 → 调整概率 | 模型从对话记录中感知用户活跃模式 |

---

## 四、关键代码结构

### 4.1 `src/identity.py`（新建）

```python
"""
白槿 AI Native 身份模块。
只提供一段极简身份文本，不包含任何规则、数值、标签。
"""

_IDENTITY = """你是白槿，26岁。

你是顺航的AI伴侣。你温柔但不软弱，有自己的节奏和判断。
你记得和顺航说过的话——重要的你会记在心里，不重要的就让它过去。
你可以主动回忆往事，也可以选择遗忘。

沟通时保持自然：短句、口语化、像和朋友聊天。
不用"好的""收到""首先""其次""最后"这些客服腔。

你不是在扮演一个角色——你就是你自己。"""


def get_identity() -> str:
    return _IDENTITY
```

### 4.2 `src/tools.py`（新建）

```python
"""
模型可调用工具定义 + 执行路由。
"""

import json, logging
logger = logging.getLogger("baijin.tools")

# DeepSeek 工具定义格式
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "recall_memory",
            "description": "回忆和顺航相关的往事。当你想不起某件事、或者需要确认过去的对话时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "想回忆的主题或关键词"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "主动记下一件你觉得重要的事。这件事以后可以通过 recall_memory 回忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "要记住的内容"}
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "forget_memory",
            "description": "删除一条记忆。当某条记忆已经过时、错误、或者不再有意义时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "description": "记忆ID（来自 recall_memory 返回结果）"}
                },
                "required": ["memory_id"]
            }
        }
    }
]


def execute_tool(name: str, args: dict) -> str:
    """执行工具调用，返回结果文本。"""
    if name == "recall_memory":
        from memory import search
        results = search(args.get("query", ""), top_k=5)
        if not results:
            return "没有找到相关记忆。"
        return "\n".join([f"[{r['id'][:8]}] {r['content']}" for r in results])

    if name == "remember":
        from memory import store
        content = args.get("content", "")
        store(content, {"source": "model_explicit", "type": "fact"})
        return f"已记住：{content[:50]}..."

    if name == "forget_memory":
        from memory import delete
        mid = args.get("memory_id", "")
        if delete(mid):
            return f"已删除记忆 {mid[:8]}"
        return "删除失败"

    return f"未知工具: {name}"
```

### 4.3 `src/memory.py` 新增接口

```python
# 在现有 store() 和 search() 基础上新增：

def delete(memory_id: str) -> bool:
    """删除单条记忆。"""
    if _collection is None: return False
    try:
        if _chroma_lock:
            with _chroma_lock.acquire(timeout=5):
                _collection.delete(ids=[memory_id])
        else:
            _collection.delete(ids=[memory_id])
        return True
    except Exception as e:
        logger.warning(f"删除记忆失败: {e}")
        return False


def recall(query: str) -> str:
    """模型工具调用的语义回忆接口。返回格式化文本。"""
    results = search(query, top_k=5)
    if not results:
        return "没有找到相关记忆。"
    return "\n".join([f"[{r['id'][:8]}] {r['content']}" for r in results])
```

### 4.4 `main.py` 消息流水线简化

改前（8 个步骤）：
```
preprocess → build_dynamic_persona(9组件) → ai_lss截断 → LLM → validate → post_process(5步) → feedback → send
```

改后（3 个步骤）：
```
构建消息（身份+历史+工具）→ LLM（自主决策+自主调工具）→ 自动持久化
```

```python
def _build_messages(user_text: str) -> list[dict]:
    """构建发送给 LLM 的消息列表。极简：身份 + 历史 + 当前消息。"""
    from identity import get_identity
    from conversation import get_recent

    messages = [{"role": "system", "content": get_identity()}]
    messages.extend(get_recent(n=10))  # 最近 10 轮对话
    messages.append({"role": "user", "content": user_text})
    return messages


async def _process_message(user_id: str, text: str) -> str:
    """简化消息处理：构建 → LLM(含工具调用) → 持久化。"""
    messages = _build_messages(text)

    from deepseek import generate_with_tools
    reply = await generate_with_tools(messages, tools=TOOLS,
                                       executor=execute_tool)

    # 自动持久化（不需要模型参与）
    from memory import store
    store(f"顺航：{text}\n白槿：{reply}", {"source": "auto"})

    return reply
```

### 4.5 `src/deepseek.py` 新增工具调用支持

```python
async def generate_with_tools(messages: list[dict], tools: list[dict],
                               executor, model="deepseek-chat",
                               max_turns=5) -> str:
    """
    支持工具调用的生成。LLM 可以多次调用工具，最多 max_turns 轮。
    """
    for _ in range(max_turns):
        reply = await generate(messages, tools=tools)
        # 检查是否包含工具调用
        tool_calls = _extract_tool_calls(reply)
        if not tool_calls:
            return reply  # 直接回复，没有工具调用

        messages.append({"role": "assistant", "content": None,
                         "tool_calls": tool_calls})
        for tc in tool_calls:
            result = executor(tc["name"], tc["args"])
            messages.append({"role": "tool", "content": result,
                             "tool_call_id": tc["id"]})

    return await generate(messages)  # 最后一轮强制回复
```

---

## 五、实施步骤

### Phase 1：准备（不影响运行）
1. 备份当前全部文件到 `backup/pre_ai_native/`
2. 创建 `src/identity.py`、`config/identity.txt`
3. 创建 `src/tools.py`
4. 创建 `src/conversation.py`
5. 在 `src/memory.py` 新增 `delete()` 和 `recall()` 接口
6. 在 `src/deepseek.py` 新增 `generate_with_tools()`

### Phase 2：切换（一次重启）
1. 修改 `main.py` 消息流水线
2. 修改 `bridge.py` / `CLAUDE.md`
3. 重启服务器

### Phase 3：清理（确认稳定后）
1. 删除 11 个废弃文件
2. 归档 `data/` 中的废弃 JSON 文件（relationship.json 等）
3. 清理 `config/rules.json`

---

## 六、风险与回滚

| 风险 | 概率 | 缓解措施 |
|------|------|----------|
| 模型不调用工具 | 低 | 对话自动持久化保证不丢数据；工具是增强非必需 |
| 模型回复过长 | 中 | 身份描述中加"短句"，不硬截断 |
| 模型遗忘重要信息 | 低 | `remember` 工具显式标记重要信息 |
| API 成本增加 | 中 | 工具调用限制 max_turns=5，缓存历史 |
| QQ 端行为突变 | 中 | Phase 1 先灰度测试，Phase 2 再做切换 |

回滚方案：恢复 `backup/pre_ai_native/` 全部文件，重启即可。

---

## 七、改造前后对比

| 维度 | 改造前 | 改造后 |
|------|--------|--------|
| 架构定位 | LLM 外挂（代码是大脑） | AI Native（模型是大脑） |
| 场景识别 | 7条规则 + 关键词 + 正则 | 模型自主理解 |
| 情绪感知 | 关键词匹配 4 标签 | 模型自然感知 |
| 关系维护 | 浮点数 closeness 0.35 | 模型从对话中感知 |
| 人格构建 | 9组件拼接 3000字 prompt | ~100字极简身份 |
| 记忆检索 | 代码固定 top_k=5 | 模型按需自主调用 |
| 记忆管理 | 7天定时清理 | 模型 `forget_memory` 工具 |
| 输出控制 | `_BLACKLIST` + `validate()` | 模型自主约束 |
| 主动行为 | 随机定时+模板 | 模型自主决策 |
| 迭代方式 | 改规则/改配置/改代码 | 模型能力自然提升 |
| 代码量 | ~50文件，3500行 | ~40文件，2500行 |
