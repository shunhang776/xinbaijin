"""
白槿 CLI 轻量入口 — 纯 HTTP 调用，不加载 FastAPI/BGE-M3/FAISS。
用法：
  python cli.py ask "问题"
  python cli.py recent [天数]
  python cli.py recent claude
  python cli.py recent transcript             自动最新
  python cli.py recent transcript <文件名>     指定文件
  python cli.py recent transcript --list      列出文件
  python cli.py analyze                       深度分析全部历史
"""
import sys
import os
import json
import urllib.request
import urllib.parse

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from datetime import datetime, timezone, timedelta

_BEIJING = timezone(timedelta(hours=8))
_TOKEN = os.getenv("MEMORY_API_TOKEN", "baijin-memory-ask-2026")
_BASE = "http://127.0.0.1:8080"


def _get(path: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    url = f"{_BASE}{path}?{qs}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read())


def cmd_ask(query: str):
    data = _get("/memory/ask", {"q": query, "token": _TOKEN, "sender": "cli"})
    if data.get("ok"):
        sys.stdout.buffer.write(data["reply"].encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()
    else:
        print(f"❌ {data.get('error', '未知错误')}", file=sys.stderr)
        sys.exit(1)


def cmd_recent(source: str, param: str = ""):
    params: dict = {"token": _TOKEN, "source": source}
    if source == "raw":
        params["days"] = param
    elif source == "transcript":
        params["file"] = param
    data = _get("/memory/recent", params)
    if not data.get("ok"):
        print(f"❌ {data.get('error', '未知错误')}", file=sys.stderr)
        sys.exit(1)
    items = data.get("items", [])
    msgs = data.get("messages")
    if not items and not msgs:
        sys.stdout.buffer.write("（无记录）\n".encode("utf-8"))
        return
    out = []
    if source == "raw":
        for r in items:
            ts = datetime.fromtimestamp(r["time"] - 8*3600, _BEIJING).strftime("%m-%d %H:%M")
            for m in r["messages"]:
                role = "顺航" if m["role"] == "user" else "白槿"
                out.append(f"[{ts}] {role}：{m['content']}")
            out.append("")
    elif source == "claude":
        for item in items:
            ts = datetime.fromtimestamp(item["time"] - 8*3600, _BEIJING).strftime("%m-%d %H:%M")
            out.append(f"[{ts}] {item['content']}")
    elif source == "transcript":
        if msgs:
            fname = data.get("file", "")
            if not param:
                ft = data.get("file_time", 0)
                fts = datetime.fromtimestamp(ft, _BEIJING).strftime("%m-%d %H:%M") if ft else ""
                out.append(f"最新会话：{fname}  ({fts})")
                out.append("-" * 50)
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
                out.append(f"[{ts}] {role}：{m['content']}")
        else:
            if param == "--list":
                out.append(f"共 {len(items)} 个会话，显示最近 {len(items)} 个：")
                out.append("-" * 50)
            for i, item in enumerate(items):
                ts = datetime.fromtimestamp(item["time"], _BEIJING).strftime("%m-%d %H:%M")
                label = "当前会话" if i == 0 else ""
                out.append(f"[{ts}] {item['name']}  {label}  ({item['size_kb']}KB, {item['lines']} 条)")
    sys.stdout.buffer.write("\n".join(out).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：")
        print("  python cli.py ask \"问题\"")
        print("  python cli.py recent [天数]")
        print("  python cli.py recent claude")
        print("  python cli.py recent transcript            自动最新")
        print("  python cli.py recent transcript <文件名>     指定文件")
        print("  python cli.py recent transcript --list      列出文件")
        print("  python cli.py analyze                       深度分析全部历史")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "ask":
        if len(sys.argv) < 3:
            print("❌ python cli.py ask \"你的问题\"", file=sys.stderr)
            sys.exit(1)
        cmd_ask(sys.argv[2])
    elif cmd == "recent":
        sub = sys.argv[2] if len(sys.argv) > 2 else ""
        if sub == "claude":
            cmd_recent("claude")
        elif sub == "transcript":
            param = sys.argv[3] if len(sys.argv) > 3 else ""
            cmd_recent("transcript", param)
        else:
            cmd_recent("raw", sub if sub else "3")
    elif cmd == "analyze":
        print("深度分析全部历史对话（LLM 调用约 5-10 秒）...")
        data = _get("/memory/analyze", {"token": _TOKEN})
        if data.get("ok"):
            print(f"✅ 分析完成，{data.get('files', '?')} 个文件，{data.get('messages', '?')} 条消息")
            print()
            result = data.get("result", "")
            sys.stdout.buffer.write(result.encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()
        else:
            print(f"❌ {data.get('error', '未知错误')}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"❌ 未知命令: {cmd}", file=sys.stderr)
        sys.exit(1)
