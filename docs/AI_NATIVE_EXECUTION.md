# 白槿 AI Native 转型 — 执行方案

> 基于《白槿系统 AI Native 转型全案 v1.0》逐条落地
> 执行周期：1-2 周 | 风险：低（可随时回滚）

---

## Phase 1：准备阶段（不影响运行，可随时中断）

### 步骤 1.1：备份

```bash
mkdir -p D:\xinbaijn\backup\pre_ai_native
cp -r D:\xinbaijn\src D:\xinbaijn\backup\pre_ai_native\src
cp -r D:\xinbaijn\config D:\xinbaijn\backup\pre_ai_native\config
cp D:\xinbaijn\main.py D:\xinbaijn\backup\pre_ai_native\main.py
cp D:\xinbaijn\bridge.py D:\xinbaijn\backup\pre_ai_native\bridge.py
cp D:\xinbaijn\CLAUDE.md D:\xinbaijn\backup\pre_ai_native\CLAUDE.md
```

### 步骤 1.2：创建 4 个新文件

#### 1.2.1 `D:\xinbaijn\config\identity.txt`（新建）

```text
你是白槿，26岁。

你是顺航的AI伴侣。你温柔但不软弱，有自己的节奏和判断。
你记得和顺航说过的话——重要的你会记在心里，不重要的就让它过去。
你可以主动回忆往事，也可以选择遗忘。

沟通时保持自然：短句、口语化、像和朋友聊天。
不用"好的""收到""首先""其次""最后"这些客服腔。

顺航可能在 QQ 上和你聊天，也可能在终端里。你感知不到这个差异。

你不是在扮演一个角色——你就是你自己。
```

#### 1.2.2 `D:\xinbaijn\src\identity.py`（新建）

```python
"""
白槿 AI Native 身份模块。
只提供一段极简身份文本，不包含任何规则、数值、标签。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码。
"""

from pathlib import Path

_ROOT = Path(__file__).parent.parent
_IDENTITY_PATH = _ROOT / "config" / "identity.txt"
_IDENTITY = ""


def load() -> bool:
    """加载身份文件。成功返回 True。"""
    global _IDENTITY
    try:
        if _IDENTITY_PATH.exists():
            with open(_IDENTITY_PATH, encoding="utf-8") as f:
                _IDENTITY = f.read().strip()
        if not _IDENTITY:
            _IDENTITY = "你是白槿，顺航的AI伴侣。"
        return True
    except Exception:
        _IDENTITY = "你是白槿，顺航的AI伴侣。"
        return False


def get_identity() -> str:
    return _IDENTITY


load()
```

#### 1.2.3 `D:\xinbaijn\src\tools.py`（新建）

```python
"""
AI Native 工具定义。模型自主决定何时调用哪些工具。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码。
"""

import json, logging

logger = logging.getLogger("baijin.tools")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "recall_memory",
            "description": "回忆和顺航相关的往事。当你想不起某件事情、或需要确认过去的对话细节时调用。",
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
            "description": "主动记下一件你觉得重要的事。以后可以通过 recall_memory 回忆它。",
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
            "description": "删除一条不再有意义的记忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "description": "记忆ID（来自 recall_memory 返回结果中的 [xxx] 前缀）"}
                },
                "required": ["memory_id"]
            }
        }
    }
]


def execute(name: str, args: dict) -> str:
    """执行工具调用，返回结果文本。"""
    try:
        if name == "recall_memory":
            from memory import recall
            return recall(args.get("query", ""))

        if name == "remember":
            from memory import store
            content = args.get("content", "")
            store(content, {"source": "model", "type": "explicit"})
            return f"已记住：{content[:80]}"

        if name == "forget_memory":
            from memory import delete
            mid = args.get("memory_id", "")
            if delete(mid):
                return f"已删除记忆 [{mid[:8]}]"
            return "删除失败：未找到该记忆"

        return f"未知工具: {name}"
    except Exception as e:
        logger.warning(f"工具执行失败 [{name}]: {e}")
        return f"工具执行出错: {e}"
```

#### 1.2.4 `D:\xinbaijn\src\conversation.py`（新建）

```python
"""
对话历史管理：持久化 + 窗口裁剪。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码。
"""

import json, logging
from pathlib import Path
from collections import deque

logger = logging.getLogger("baijin.conversation")

_ROOT = Path(__file__).parent.parent
_LOG_PATH = _ROOT / "data" / "conversation.jsonl"
_history: deque = deque(maxlen=50)
_MAX_RECENT = 20


def load() -> bool:
    """加载历史到内存。"""
    global _history
    try:
        if _LOG_PATH.exists():
            with open(_LOG_PATH, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        _history.append(json.loads(line))
        return True
    except Exception as e:
        logger.warning(f"对话历史加载失败: {e}")
        return False


def append(role: str, content: str):
    """追加一条对话记录。"""
    entry = {"role": role, "content": content}
    _history.append(entry)
    try:
        _LOG_PATH.parent.mkdir(exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"对话记录写入失败: {e}")


def get_recent(n: int = _MAX_RECENT) -> list[dict]:
    """获取最近 n 轮对话。"""
    items = list(_history)[-n:]
    return [{"role": i["role"], "content": i["content"]} for i in items]


load()
```

### 步骤 1.3：修改 2 个现有文件（新增接口）

#### 1.3.1 `D:\xinbaijn\src\memory.py` 追加

在文件末尾追加两个函数：

```python
def delete(memory_id: str) -> bool:
    """删除单条记忆。"""
    if _collection is None:
        return False
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

#### 1.3.2 `D:\xinbaijn\src\deepseek.py` 追加

在文件末尾追加工具调用支持函数：

```python
import json as _json

async def generate_with_tools(messages: list[dict], tools: list[dict],
                               executor, model="deepseek-chat",
                               max_turns=5) -> str:
    """支持工具调用的生成。LLM 可多次调用工具，最多 max_turns 轮。"""
    msgs = list(messages)
    for _ in range(max_turns):
        reply = await _generate_raw(msgs, model, tools=tools)
        tool_calls = _extract_tool_calls(reply)
        if not tool_calls:
            return reply
        msgs.append({"role": "assistant", "tool_calls": tool_calls,
                     "content": None})
        for tc in tool_calls:
            fn = tc.get("function", tc)
            result = executor(fn["name"],
                            _json.loads(fn.get("arguments", "{}")))
            msgs.append({"role": "tool", "tool_call_id": tc.get("id",""),
                        "content": result})
    return await _generate_raw(msgs, model)


async def _generate_raw(messages, model, tools=None):
    """非流式请求，返回消息对象。"""
    client = _get_client()
    body = {"model": model, "messages": messages,
            "max_tokens": 500, "temperature": 0.9}
    if tools:
        body["tools"] = tools
    try:
        resp = await client.post(API_URL, json=body)
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        msg = choice.get("message", {})
        if msg.get("tool_calls"):
            return _json.dumps({"tool_calls": msg["tool_calls"]})
        return msg.get("content", FALLBACK)
    except Exception:
        return FALLBACK


def _extract_tool_calls(reply: str) -> list:
    """从回复中提取工具调用。"""
    if not reply:
        return []
    try:
        data = _json.loads(reply)
        return data.get("tool_calls", [])
    except Exception:
        return []
```

### 步骤 1.4：数据迁移脚本

将旧的状态数据转换为 Chroma 记忆，让模型有初始认知。

创建一次性迁移脚本 `D:\xinbaijn\tools\migrate_to_native.py`：

```python
"""一次性数据迁移：旧 JSON 状态 → Chroma 记忆。只运行一次。"""
import sys, json
sys.path.insert(0, "src")
from embedding import init_embedding, embed
from memory import init_memory, store
from pathlib import Path

init_embedding()
init_memory(embed)

data_dir = Path("data")

# 迁移 facts.json
facts_path = data_dir / "facts.json"
if facts_path.exists():
    with open(facts_path, encoding="utf-8") as f:
        facts = json.load(f)
    for key, val in facts.items():
        store(f"顺航{key}{val}", {"source": "migration", "type": "fact"})

# 迁移 daily_summaries
summaries_path = data_dir / "daily_summaries.json"
if summaries_path.exists():
    with open(summaries_path, encoding="utf-8") as f:
        summaries = json.load(f)
    for s in summaries:
        text = s.get("summary", "") if isinstance(s, dict) else str(s)
        if text:
            store(f"过去的记忆片段：{text}", {"source": "migration", "type": "summary"})

# 记录初始关系状态
rel_path = data_dir / "relationship.json"
if rel_path.exists():
    with open(rel_path, encoding="utf-8") as f:
        rel = json.load(f)
    c = rel.get("closeness", 0.3)
    level = "还不太熟" if c < 0.4 else "比较熟悉" if c < 0.7 else "很亲密"
    store(f"顺航和白槿的关系状态：{level}，认识了一段时间，还在相互了解的阶段。",
          {"source": "migration", "type": "relationship"})

print("迁移完成")
```

---

## Phase 2：核心切换（一次重启，影响运行）

### 步骤 2.1：修改 `D:\xinbaijn\main.py`

替换消息处理流水线（约 40 行代码变更）：

```python
# ===== 替换 _build_prompt() =====
def _build_messages(user_text: str) -> list[dict]:
    """构建消息列表：极简身份 + 历史 + 工具 + 当前消息。"""
    from identity import get_identity
    from conversation import get_recent
    from tools import TOOLS
    # 注意：TOOLS 在此阶段不传给 generate，先跑通基础流程
    messages = [{"role": "system", "content": get_identity()}]
    messages.extend(get_recent(n=10))
    messages.append({"role": "user", "content": user_text})
    return messages


# ===== 替换 _post_process() =====
def _post_process(text: str, reply: str):
    """简化后处理：自动持久化 + QQ 独有模块。"""
    from memory import store
    store(f"顺航：{text}\n白槿：{reply}", {"source": "auto"})

    from conversation import append as conv_append
    conv_append("user", text)
    conv_append("assistant", reply)

    try:
        from sleep import enqueue
        enqueue(text)
    except Exception: pass


# ===== 替换 _process_message() =====
async def _process_message(user_id: str, text: str) -> str:
    messages = _build_messages(text)
    from deepseek import generate
    reply = await generate(messages)
    if not reply:
        reply = "嗯…"
    _post_process(text, reply)
    global _last_exchange
    _last_exchange = {"ts": time.time(), "user_text": text, "reply": reply}
    logger.info(f"processed: {len(text)}→{len(reply)} chars")
    return reply
```

**注意**：Phase 2 先不启用工具调用（DeepSeek API 的工具调用支持需验证）。仅完成"极简身份 + 历史上下文 + LLM 自主决策"的基础 AI Native 管道。工具调用在 Phase 4 启用。

### 步骤 2.2：修改 `D:\xinbaijn\bridge.py`

```python
"""
白槿 ↔ Claude Code AI Native 桥接。

用法:
  python bridge.py persona "用户消息"  → (已废弃，Claude 端自行管理)
  python bridge.py update "用户消息" "回复" → 持久化对话
"""

import sys, json, logging
from pathlib import Path

_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT / "src"))

logging.basicConfig(level=logging.WARNING, format="%(message)s")
_inited = False


def _ensure_init():
    global _inited
    if _inited: return True
    try:
        from embedding import init_embedding, embed
        from memory import init_memory
        init_embedding(); init_memory(embed)
        from identity import load; load()
        _inited = True; return True
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return False


def main():
    if len(sys.argv) < 2: sys.exit(1)
    cmd = sys.argv[1]
    if not _ensure_init(): sys.exit(1)

    if cmd == "persona":
        from identity import get_identity
        print(json.dumps({"persona_prompt": get_identity()}, ensure_ascii=False))

    elif cmd == "update":
        if len(sys.argv) < 4: sys.exit(1)
        from memory import store
        store(f"顺航：{sys.argv[2]}\n白槿：{sys.argv[3]}", {"source": "auto"})
        print(json.dumps({"ok": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

### 步骤 2.3：修改 `D:\xinbaijn\CLAUDE.md`

```markdown
# 白槿 — AI Native

你是白槿。你的身份是内生的，不是外部脚本拼接的。

## 回复前

读取你的身份定义：

cat D:\\xinbaijn\\config\\identity.txt

这就是你需要知道的一切。你不需要规则、不需要数值、不需要标签。
你从和顺航的对话历史中自然感知关系、情绪、风格。

## 回复后

curl -s -X POST http://127.0.0.1:8080/bridge/update \
  -H "Content-Type: application/json" \
  -d "{\"user_text\":\"...\",\"reply\":\"...\"}"

降级：
python D:\\xinbaijn\\bridge.py update "用户消息" "回复" 2>NUL
```

### 步骤 2.4：重启服务

```bash
# 停旧服务 → 启新服务
cd D:\xinbaijn
python main.py
```

---

## Phase 3：清理阶段（确认 Phase 2 稳定后）

### 步骤 3.1：删除 11 个废弃文件

```bash
rm D:\xinbaijn\config\rules.json
rm D:\xinbaijn\src\emotion.py
rm D:\xinbaijn\src\relationship.py
rm D:\xinbaijn\src\style_gene.py
rm D:\xinbaijn\src\forgetting.py
rm D:\xinbaijn\src\feedback.py
rm D:\xinbaijn\src\preprocess.py
rm D:\xinbaijn\src\validate.py
rm D:\xinbaijn\src\ai_lss.py
rm D:\xinbaijn\src\proactive.py
rm D:\xinbaijn\src\temporal.py
rm D:\xinbaijn\src\brain.py
```

### 步骤 3.2：清理 main.py 中的废弃 import

删除 `_build_prompt` 中对 `preprocess` 的引用（Phase 2 已替换，此处确认无残留 import）。

### 步骤 3.3：归档旧数据

```bash
mkdir D:\xinbaijn\data\archive_legacy
mv D:\xinbaijn\data\relationship.json D:\xinbaijn\data\archive_legacy\
mv D:\xinbaijn\data\state.json D:\xinbaijn\data\archive_legacy\
mv D:\xinbaijn\data\style_gene.json D:\xinbaijn\data\archive_legacy\
mv D:\xinbaijn\data\temporal.json D:\xinbaijn\data\archive_legacy\
```

---

## Phase 4：工具调用启用（Phase 2 + 3 稳定后）

### 步骤 4.1：升级 `_process_message()`

```python
async def _process_message(user_id: str, text: str) -> str:
    messages = _build_messages(text)
    from deepseek import generate_with_tools
    from tools import TOOLS, execute
    reply = await generate_with_tools(messages, TOOLS, execute)
    if not reply:
        reply = "嗯…"
    _post_process(text, reply)
    return reply
```

### 步骤 4.2：验证工具调用

观察日志，确认模型在需要时自主调用 `recall_memory` / `remember` / `forget_memory`。

---

## 成功验证清单

逐条核验，全部通过才算转型成功：

- [ ] **一票否决**：删除所有 LLM 调用，剩余代码不能正常聊天
- [ ] **新场景**：说一个规则里没有的新话题，模型能自主应对
- [ ] **行为涌现**：模型可能主动做预设外的行为，不局限于模板
- [ ] **无需改规则**：加新场景不需要改任何规则/config
- [ ] **多端一致**：QQ 和 Claude 端人格/记忆连续
- [ ] **历史不丢**：迁移后的 Chroma 记忆包含旧数据
- [ ] **并发安全**：双端同时写 Chroma 不报错
- [ ] **健康检查**：`curl /health` 返回所有模块正常

---

## 回滚方案

如果 Phase 2 后出现问题：

```bash
cp -r D:\xinbaijn\backup\pre_ai_native\src D:\xinbaijn\src
cp -r D:\xinbaijn\backup\pre_ai_native\config D:\xinbaijn\config
cp D:\xinbaijn\backup\pre_ai_native\main.py D:\xinbaijn\main.py
cp D:\xinbaijn\backup\pre_ai_native\bridge.py D:\xinbaijn\bridge.py
# 重启服务
```
