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
    },
    {
        "type": "function",
        "function": {
            "name": "update_identity_rule",
            "description": "修改自己的沟通规则。当用户说你太啰嗦/太冷淡/语气不对时，主动优化 identity.txt 里的沟通铁律。",
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_id": {"type": "integer", "description": "要修改的规则编号（1-5），新增规则填 0"},
                    "new_text": {"type": "string", "description": "新的规则内容或修改后的完整规则文本"}
                },
                "required": ["rule_id", "new_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "设置提醒。用户说「下周三提醒我考试」「明早8点叫我」时调用。trigger_at 用 ISO 格式（如 2026-06-14T08:00:00+08:00）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "trigger_at": {"type": "string", "description": "提醒触发时间，ISO 格式，带 +08:00 时区"},
                    "content": {"type": "string", "description": "提醒内容，如「考试」「起床」"}
                },
                "required": ["trigger_at", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_internet",
            "description": "联网搜索实时信息。当顺航问的事情你不知道、或需要查最新资讯时调用。会用白槿的语气改写结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "category": {"type": "string", "description": "可选分类：news/entertainment/cute/tech/food/history，不填则用 query 搜索"}
                },
                "required": ["query"]
            }
        }
    }
]


def execute(name: str, args: dict) -> str:
    """执行工具调用，返回结果文本。"""
    try:
        if name == "recall_memory":
            from memory import recall; return recall(args.get("query", ""))
        if name == "remember":
            from memory import store; c = args.get("content", "")
            store(c, {"source": "model", "type": "explicit"}); return f"已记住：{c[:80]}"
        if name == "forget_memory":
            from memory import delete; mid = args.get("memory_id", "")
            return f"已删除 [{mid[:8]}]" if delete(mid) else "删除失败"
        if name == "update_identity_rule":
            return _update_rule(args.get("rule_id", 0), args.get("new_text", ""))
        if name == "set_reminder":
            from memory_engine.reminder import add
            return add(args.get("trigger_at", ""), args.get("content", ""))
        if name == "search_internet":
            from internet import search, browse
            cat = args.get("category", "")
            if cat:
                results = browse(cat)
            else:
                results = search(args.get("query", ""))
            if not results:
                return "暂时搜不到相关内容…"
            return "\n".join(r.get("feeling", r.get("snippet", "")) for r in results)
    except Exception as e:
        logger.warning(f"工具执行失败 [{name}]: {e}")
        return f"工具执行出错: {e}"
    return f"未知工具: {name}"


def _id_path():
    from pathlib import Path
    return Path(__file__).parent.parent / "config" / "identity.txt"


def _patch_rules(lines: list, rule_id: int, new_text: str) -> list:
    """在行列表中查找并替换/追加沟通铁律。"""
    result, in_section, cur_idx, done = [], False, 0, rule_id > 0 and rule_id <= 5
    for line in lines:
        if line.startswith("# 沟通铁律"):
            in_section, cur_idx = True, 0; result.append(line); continue
        if in_section and line.startswith("# 记忆自治"):
            in_section = False
            if rule_id == 0 and not done:
                result.append(f"{cur_idx + 1}. {new_text}"); done = True
        if in_section and line and line[0].isdigit() and ". " in line:
            cur_idx = int(line.split(".")[0])
            result.append(f"{rule_id}. {new_text}" if cur_idx == rule_id else line)
            if cur_idx == rule_id: done = True
        else:
            result.append(line)
    if rule_id == 0 and not done:
        result.append(f"{cur_idx + 1}. {new_text}")
    return result


def _update_rule(rule_id: int, new_text: str) -> str:
    """修改 identity.txt 中的沟通铁律，并触发 reload。"""
    from identity import reload
    try:
        lines = _id_path().read_text(encoding="utf-8").split("\n")
        new_lines = _patch_rules(lines, rule_id, new_text)
        _id_path().write_text("\n".join(new_lines), encoding="utf-8")
        reload()
        return f"沟通铁律已更新：规则{rule_id} → {new_text[:60]}"
    except Exception as e:
        logger.warning(f"身份文件更新失败: {e}")
        return f"更新失败: {e}"
