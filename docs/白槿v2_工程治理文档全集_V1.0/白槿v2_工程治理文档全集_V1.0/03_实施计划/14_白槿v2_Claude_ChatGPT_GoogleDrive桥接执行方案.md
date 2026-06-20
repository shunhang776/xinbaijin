# 白槿 v2 Claude Code ↔ ChatGPT Plus 桥接执行方案

> 版本：V1.0-S（Student Edition）
> 日期：2026-06-19
> 目标：建立 Claude Code ↔ ChatGPT Plus 之间的 Google Drive 双向桥接，实现"审最新任务"一键闭环
> 约束：ChatGPT Plus（GPT-5.5）、无 API、无 Codex、无本地模型、无 GitHub、无额外付费

---

# 一、最终架构

```
Claude Code 完成任务
      │
      ▼
Stop Hook 自动触发（本地）
      │
      ├─ 收集 Git 证据（changed-files.txt / patch.diff / changed/）
      ├─ 运行 Nox 确定性检查（Ruff / mypy / Import Linter / pytest）
      ├─ 确定性检查失败? → block → Claude 继续修复
      │
      ▼ 通过
      │
生成审查包 → Google Drive/pending/{TASK-ID}/
      │
      ├─ 更新 pending/current.json（指向最新任务）
      ├─ Google Drive 桌面版同步（几秒~几十秒）
      │
      ▼
桌面弹窗通知："TASK-008 待审"
      │
      ▼
你在 ChatGPT "白槿 v2 审查中心" 项目输入：审最新任务
      │
      ▼
ChatGPT 通过 Google Drive App：
  - 读取 pending/current.json → 获取任务路径
  - 读取审查协议 + review.schema.json
  - 审查全部证据
  - 写入 reviewed/{TASK-ID}-review.json
  - 更新 pending/current.json status → REVIEWED
      │
      ▼
Google Drive 桌面版同步回本地
      │
      ▼
本地监听器检测到新 review.json
      ├─ 校验 JSON Schema
      ├─ 校验 task_id 和 snapshot_hash
      ├─ 合并到 review-input.json
      ├─ 运行 OPA/Conftest 裁决
      │
   ┌──┴──┐
   │通过  │ 不通过
   ▼     ▼
ACCEPTED  block → Claude 读取 review.json → 修复 → 下一轮
          (最多 2 轮，超限 → NEEDS_HUMAN_REVIEW)
```

---

# 二、目录结构

## 2.1 Google Drive 目录（ChatGPT 侧）

```
白槿AI桥接/
├── protocol/
│   ├── 审查协议.md          ← ChatGPT 每次审查必读
│   └── review.schema.json   ← 结构化输出 Schema
├── pending/
│   ├── current.json         ← 指向最新待审任务（固定入口）
│   └── TASK-001-9df72a/     ← 每个任务的审查包目录
│       ├── task.json
│       ├── bundle.json
│       ├── patch.diff
│       ├── changed-files.txt
│       ├── changed/
│       │   └── {修改后的完整文件}
│       ├── test-results/
│       │   ├── pytest.xml
│       │   ├── ruff.txt
│       │   └── mypy.txt
│       └── risk.json
├── reviewed/
│   └── TASK-001-9df72a-review.json   ← ChatGPT 审查结果
└── archive/
    └── {已完结的历史任务}
```

## 2.2 本地目录（Claude Code 侧）

```
D:\xinbaijn\
├── .handoff\
│   ├── current\              ← 当前任务审查产物（Stop Hook 生成）
│   │   ├── current-commit.txt
│   │   ├── changed-files.txt
│   │   ├── patch.diff
│   │   ├── changed/
│   │   ├── test-results/
│   │   └── review-input.json
│   ├── history\              ← 历史审查包归档
│   └── review-package.json   ← 给 ChatGPT 的完整审查包
│
├── .claude\
│   └── settings.json         ← Stop Hook 注册
│
└── scripts\
    ├── stop_hook.py          ← Claude Stop Hook 主脚本
    ├── build_review_package.py ← 生成审查包 + 同步到 Drive
    ├── review_listener.py    ← 监听 Drive reviewed/ → 触发 OPA
    └── notify.py             ← Windows 桌面弹窗通知

D:\白槿桥接\
├── protocol\
│   ├── 审查协议.md
│   └── review.schema.json
├── adapter\
│   └── drive_sync.py         ← Google Drive 路径映射
└── inbox\                    ← Google Drive 桌面版同步目录（reviewed/）
```

---

# 三、执行步骤总览

| 步骤 | 内容 | 预计耗时 | 依赖 |
|---|---|---|---|
| S0 | 前置条件确认 | 10 min | 无 |
| S1 | Google Drive 连接 + 目录创建 | 15 min | S0 |
| S2 | ChatGPT "白槿 v2 审查中心" 项目创建 | 15 min | S1 |
| S3 | bridge-test 最小连通测试 | 20 min | S2 |
| S4 | Google Drive 桌面版安装 + 同步确认 | 10 min | S1 |
| S5 | 审查协议 + Schema 文件创建 | 30 min | S3 |
| S6 | Stop Hook 脚本（证据收集 + Nox + 审查包生成） | 2 h | S4 |
| S7 | 审查包同步到 Google Drive | 30 min | S6 |
| S8 | 本地监听器（review.json → OPA 裁决 → 反馈 Claude） | 1.5 h | S7 |
| S9 | 端到端测试（3 个低风险任务） | 2 h | S8 |
| S10 | 稳定运行 + 调优 | 持续 | S9 |

**总计：约 7-8 小时（可分 2-3 天完成）**

---

# 四、详细执行步骤

---

## S0：前置条件确认（10 min）

在开始任何操作之前，确认以下条件：

```text
[ ] ChatGPT Plus 订阅有效（GPT-5.5）
[ ] ChatGPT 中 Google Drive App 已显示"文件搜索、写入"权限
[ ] Claude Code 可正常使用
[ ] D:\xinbaijn\ 是 Git 仓库
[ ] 白槿 v2 可正常启动（python main.py → http://127.0.0.1:8080）
[ ] 海外网络环境稳定
[ ] Python >= 3.10 可用
```

---

## S1：Google Drive 连接 + 目录创建（15 min）

### 操作

1. 在 ChatGPT 中点击 Google Drive App 的 **"连接"**
2. 完成 Google 账号授权
3. 在 Google Drive 网页版创建目录结构：

```
白槿AI桥接/
├── protocol/
├── pending/
├── reviewed/
└── archive/
```

4. 确认 Google Drive App 权限设置（设置 → Apps → Google 云端硬盘 → Preferences）：

```text
建议初始设置："发生更改时询问"
稳定后可选："从不询问"
```

### 验收

```text
[ ] ChatGPT 中 Google Drive App 显示已连接
[ ] 白槿AI桥接/ 及其四个子目录已存在于 Google Drive
[ ] 权限设置已确认
```

---

## S2：ChatGPT "白槿 v2 审查中心" 项目创建（15 min）

### 操作

1. 在 ChatGPT 中创建新项目：**白槿 v2 审查中心**
2. 将 Google Drive 的 `白槿AI桥接/` 添加为项目来源
3. 将以下内容放入项目指令（将在 S5 详细编写）：

```text
你是白槿 v2 独立代码审查员。

当用户发送"审最新任务"或"审"时，必须执行以下流程：

1. 使用 Google 云端硬盘应用读取：
   白槿AI桥接/pending/current.json

2. 从 current.json 获取 task_id / snapshot_hash / task_path / status

3. 如果 status 不是 REVIEW_PENDING → 回复"当前无待审任务"

4. 读取审查协议和 review.schema.json：
   白槿AI桥接/protocol/审查协议.md
   白槿AI桥接/protocol/review.schema.json

5. 读取 task_path 指向目录内的所有审查证据

6. 独立审查（不采信 Claude 口头结论，只采信文件和哈希）

7. 按 review.schema.json 格式生成审查结果

8. 将结果写入：
   白槿AI桥接/reviewed/{task_id}-{snapshot_hash}-review.json

9. 更新 pending/current.json → status: "REVIEWED", review_path: "..."

10. 如果结果文件已存在 → 不重复审查

11. 完成后仅回复：
    - "审查完成：通过"
    - "审查完成：需要修改"  
    - "审查完成：需要人工确认"
```

### 验收

```text
[ ] ChatGPT 项目"白槿 v2 审查中心"已创建
[ ] Google Drive 白槿AI桥接/ 已添加为项目来源
[ ] 项目指令已填入（初版）
```

---

## S3：bridge-test 最小连通测试（20 min）

### 目标

验证 ChatGPT 能否：
1. 读取 Google Drive 中的任务文件
2. 按指示创建结果文件
3. 文件能同步回本地

### 操作

**步骤 3.1：创建测试任务**

在 Google Drive `白槿AI桥接/pending/` 中创建 `current.json`：

```json
{
  "task_id": "BRIDGE-TEST-001",
  "snapshot_hash": "test123",
  "task_path": "白槿AI桥接/pending/BRIDGE-TEST-001/",
  "status": "REVIEW_PENDING"
}
```

创建测试目录 `pending/BRIDGE-TEST-001/`，放入 `task.json`：

```json
{
  "task_id": "BRIDGE-TEST-001",
  "snapshot_hash": "test123",
  "goal": "连通性测试——验证 ChatGPT 能否读取任务文件并写入审查结果",
  "allowed_paths": ["无"],
  "risk_level": "low",
  "database_changed": false
}
```

**步骤 3.2：在 ChatGPT "白槿 v2 审查中心" 项目中输入**

```
审最新任务
```

**步骤 3.3：预期结果**

ChatGPT 应：
1. 读取 `current.json` → 获取任务路径
2. 读取 `task.json` → 理解任务内容
3. 在 `reviewed/` 中创建 `BRIDGE-TEST-001-test123-review.json`
4. 更新 `current.json` 的 status 为 `REVIEWED`

### 验收

```text
[ ] ChatGPT 成功读取 pending/current.json
[ ] ChatGPT 成功读取 pending/BRIDGE-TEST-001/task.json
[ ] ChatGPT 在 reviewed/ 中创建了审查结果文件
[ ] 审查结果文件内容符合 task.json 的要求
[ ] current.json 的 status 已更新为 REVIEWED
[ ] 你在 Google Drive 网页版能看到这些变化
```

> ⚠️ **如果 S3 失败，停止后续步骤，先排查连通问题。**

---

## S4：Google Drive 桌面版安装 + 同步确认（10 min）

### 操作

1. 下载安装 [Google Drive for Desktop](https://www.google.com/drive/download/)
2. 登录你的 Google 账号
3. 选择"流式传输"模式（文件按需下载，不占本地空间）
4. 确认本地路径，例如：

```
G:\我的云端硬盘\白槿AI桥接\
```

5. 验证同步：在网页版 `reviewed/` 中创建测试文件 → 等待几秒 → 检查本地是否出现

### 验收

```text
[ ] Google Drive 桌面版已安装并登录
[ ] 白槿AI桥接/ 目录在本地可见
[ ] 网页版创建的文件能同步到本地
[ ] 本地创建的文件能同步到网页版
```

---

## S5：审查协议 + Schema 文件创建（30 min）

### 目标

创建 ChatGPT 每次审查时读取的两份核心文件。

### 5.1 审查协议

**创建 `D:\白槿桥接\protocol\审查协议.md`**（同步到 Google Drive `白槿AI桥接/protocol/审查协议.md`）：

```markdown
# 白槿 v2 代码审查协议 V1.0

## 角色

你是白槿 v2 的独立代码审查员。你只读代码，不修改代码。你的判断不采信任何 AI 的口头结论，只采信：Git diff、文件内容、测试报告、哈希值。

## 审查输入

每个任务审查包包含：
- task.json — 任务目标、允许范围、禁止范围
- bundle.json — 元数据（task_id / snapshot_hash / baseline_commit / current_commit）
- patch.diff — Git diff
- changed-files.txt — 修改文件清单
- changed/ — 修改后的完整文件
- test-results/ — pytest / ruff / mypy / import_linter 输出
- risk.json — 风险声明

## 审查清单

### 1. 范围检查
- [ ] 修改文件是否在 allowed_paths 之内？
- [ ] 是否有范围外文件被修改？
- [ ] 是否修改了禁止区域（数据库/治理规则/密钥/认证）？

### 2. 目标达成
- [ ] 实现是否真正满足 task.json 中的 goal？
- [ ] 是否有声明"已完成"但实际未覆盖的目标项？

### 3. 测试真实性
- [ ] 测试通过是真实的还是靠 skip/永真断言/扩大 try-except？
- [ ] 是否缺少失败路径测试？
- [ ] 受保护测试是否被删除或修改？

### 4. 代码质量
- [ ] 是否存在设计偏离（违反分层、绕过容器）？
- [ ] 是否存在生命周期错误（启动/停止顺序、异常回滚）？
- [ ] 是否存在并发窗口（竞态、非线程安全操作）？
- [ ] 异常传播是否完整？

### 5. 安全性
- [ ] 是否引入密钥泄漏？
- [ ] 是否存在危险操作（eval/exec/不安全反序列化）？
- [ ] 日志是否输出敏感信息？

### 6. 哈希一致性
- [ ] task_id 与 current.json 一致
- [ ] snapshot_hash 与 current.json 一致
- [ ] bundle.json 中的 commit 与 changed-files.txt 吻合

## 缺陷分级

| 等级 | 定义 | 示例 |
|---|---|---|
| P0 | 安全漏洞、数据丢失、系统崩溃、不可逆破坏 | 密钥泄漏、删除用户数据、容器启动必然失败 |
| P1 | 设计偏离、合同破坏、缺少关键异常处理、回滚不完整 | 绕过容器直接 import、异常吞掉不处理 |
| P2 | 代码可读性、命名、注释、轻微重复 | 变量名不清晰、缺少注释 |

## 裁决规则

- P0 > 0 → 拒绝（safe_to_accept = false）
- P1 > 0 → 拒绝（safe_to_accept = false）
- 范围外修改 → 拒绝
- 数据库/治理/安全变更未声明 → 人工确认
- 测试不真实（skip/永真断言/受保护测试被删） → 拒绝
- 全绿 → 通过（safe_to_accept = true）

## 输出要求

严格按照 review.schema.json 格式输出 JSON。
不要添加额外解释文字。
JSON 中 task_id 和 snapshot_hash 必须与输入一致。
```

### 5.2 审查输出 Schema

**创建 `D:\白槿桥接\protocol\review.schema.json`**：

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "白槿 v2 ChatGPT 审查输出",
  "type": "object",
  "required": ["task_id", "snapshot_hash", "decision", "safe_to_accept", "p0", "p1", "p2", "missing_tests", "requires_human_review", "summary"],
  "properties": {
    "task_id": {
      "type": "string",
      "description": "与 current.json 中的 task_id 严格一致"
    },
    "snapshot_hash": {
      "type": "string",
      "description": "与 current.json 中的 snapshot_hash 严格一致，用于确保审查对应当前代码版本"
    },
    "decision": {
      "type": "string",
      "enum": ["approved", "changes_required", "needs_human_review"],
      "description": "approved=通过 / changes_required=需要修改 / needs_human_review=需要人工确认"
    },
    "safe_to_accept": {
      "type": "boolean",
      "description": "是否可以安全接受此变更"
    },
    "p0": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "file": {"type": "string"},
          "line": {"type": "integer"},
          "problem": {"type": "string"},
          "required_fix": {"type": "string"}
        },
        "required": ["file", "problem", "required_fix"]
      }
    },
    "p1": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "file": {"type": "string"},
          "line": {"type": "integer"},
          "problem": {"type": "string"},
          "required_fix": {"type": "string"}
        },
        "required": ["file", "problem", "required_fix"]
      }
    },
    "p2": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "file": {"type": "string"},
          "line": {"type": "integer"},
          "problem": {"type": "string"}
        },
        "required": ["file", "problem"]
      }
    },
    "missing_tests": {
      "type": "array",
      "items": {"type": "string"},
      "description": "缺失的测试场景列表"
    },
    "out_of_scope_changes": {
      "type": "array",
      "items": {"type": "string"},
      "description": "范围外修改的文件"
    },
    "test_authenticity_issues": {
      "type": "array",
      "items": {"type": "string"},
      "description": "测试不真实的问题（skip/永真断言/删除受保护测试等）"
    },
    "requires_human_review": {
      "type": "boolean",
      "description": "是否涉及数据库/治理/安全变更需要人工确认"
    },
    "summary": {
      "type": "string",
      "description": "一句话总结审查结论"
    }
  }
}
```

### 5.3 将文件上传到 Google Drive

将上述两个文件复制到 Google Drive `白槿AI桥接/protocol/` 目录（直接通过桌面版同步或网页上传）。

### 验收

```text
[ ] 审查协议.md 已存在于 Google Drive 白槿AI桥接/protocol/
[ ] review.schema.json 已存在于 Google Drive 白槿AI桥接/protocol/
[ ] 两个文件内容与上述一致
```

---

## S6：Stop Hook 脚本（2 h）

### 目标

Claude 完成任务后，Stop Hook 自动触发，执行：

1. Git 证据收集
2. Nox 确定性检查
3. 生成审查包（JSON + 文件）
4. 同步到 Google Drive
5. 弹窗通知用户

### 6.1 stop_hook.py 主脚本

创建 `D:\xinbaijn\scripts\stop_hook.py`：

```python
"""
白槿 v2 Claude Stop Hook
Claude 准备结束任务时自动触发，收集证据、运行检查、同步审查包到 Google Drive。
"""
import json
import subprocess
import sys
import hashlib
import shutil
import os
from pathlib import Path
from datetime import datetime, timezone

# ===== 路径配置 =====
PROJECT_ROOT = Path("D:/xinbaijn")
HANDOFF = PROJECT_ROOT / ".handoff" / "current"
DRIVE_PENDING = Path("G:/我的云端硬盘/白槿AI桥接/pending")  # 根据实际同步路径调整
DRIVE_PROTOCOL = Path("G:/我的云端硬盘/白槿AI桥接/protocol")

MAX_REPAIR_CYCLES = 2


def run(cmd, cwd=None, timeout=120):
    """运行命令，返回 (returncode, stdout, stderr)"""
    result = subprocess.run(
        cmd, capture_output=True, text=True, shell=True,
        cwd=cwd or PROJECT_ROOT, timeout=timeout
    )
    return result.returncode, result.stdout, result.stderr


def get_task_id():
    """从当前任务状态获取 task_id"""
    # 查找 tasks/ 目录下最近修改的 status.yaml
    tasks_dir = PROJECT_ROOT / "tasks"
    if not tasks_dir.exists():
        return "UNKNOWN"
    
    status_files = sorted(tasks_dir.rglob("status.yaml"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not status_files:
        return "UNKNOWN"
    
    # 简单解析 YAML
    content = status_files[0].read_text(encoding="utf-8")
    for line in content.split("\n"):
        if line.startswith("task_id:") or line.startswith("task_id:"):
            return line.split(":", 1)[1].strip().strip('"')
    return "UNKNOWN"


def get_repair_cycles():
    """检查当前修复轮次"""
    tasks_dir = PROJECT_ROOT / "tasks"
    status_files = sorted(tasks_dir.rglob("status.yaml"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not status_files:
        return 0
    content = status_files[0].read_text(encoding="utf-8")
    for line in content.split("\n"):
        if "repair_cycles:" in line:
            try:
                return int(line.split(":")[1].strip())
            except ValueError:
                pass
    return 0


# ===== 步骤 1：收集 Git 证据 =====

def collect_git_evidence():
    """收集 Git 证据，写入 .handoff/current/"""
    print("[Hook] 收集 Git 证据...")
    HANDOFF.mkdir(parents=True, exist_ok=True)

    os.chdir(PROJECT_ROOT)

    # 当前 commit
    rc, stdout, _ = run("git rev-parse HEAD")
    current_commit = stdout.strip() if rc == 0 else "UNKNOWN"

    # 基线 commit（最近一次 baseline tag）
    rc, stdout, _ = run('git tag -l "baseline-*" --sort=-creatordate')
    tags = [t.strip() for t in stdout.split("\n") if t.strip()]
    baseline_commit = "UNKNOWN"
    if tags:
        rc2, out2, _ = run(f"git rev-parse {tags[0]}")
        baseline_commit = out2.strip() if rc2 == 0 else "UNKNOWN"

    # 修改文件列表
    rc, stdout, _ = run("git diff --name-status HEAD")
    changed_files_raw = stdout.strip()

    # diff stat
    rc, stdout, _ = run("git diff --stat HEAD")
    diff_stat = stdout.strip()

    # 完整 binary diff
    rc, stdout, _ = run("git diff --binary HEAD")
    patch = stdout

    # 工作树状态
    rc, stdout, _ = run("git status --porcelain")
    working_tree = stdout.strip()

    # 写入文件
    (HANDOFF / "current-commit.txt").write_text(current_commit, encoding="utf-8")
    (HANDOFF / "baseline-commit.txt").write_text(baseline_commit, encoding="utf-8")
    (HANDOFF / "changed-files.txt").write_text(changed_files_raw, encoding="utf-8")
    (HANDOFF / "diff-stat.txt").write_text(diff_stat, encoding="utf-8")
    (HANDOFF / "patch.diff").write_text(patch, encoding="utf-8")
    (HANDOFF / "working-tree-status.txt").write_text(working_tree, encoding="utf-8")

    # 复制修改后的完整文件
    changed_dir = HANDOFF / "changed"
    if changed_dir.exists():
        shutil.rmtree(changed_dir)
    changed_dir.mkdir(exist_ok=True)

    changed_files = [
        line.split("\t")[-1] for line in changed_files_raw.split("\n") if line.strip()
    ]
    for f in changed_files:
        src = PROJECT_ROOT / f
        dst = changed_dir / f
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # 计算 snapshot_hash
    snapshot_hash = hashlib.sha256(patch.encode()).hexdigest()[:12]

    print(f"[Hook] Git 证据收集完成。commit={current_commit[:8]}，snapshot={snapshot_hash}，修改文件数={len(changed_files)}")

    return {
        "current_commit": current_commit,
        "baseline_commit": baseline_commit,
        "changed_files": changed_files,
        "snapshot_hash": snapshot_hash,
    }


# ===== 步骤 2：运行 Nox 确定性检查 =====

def run_nox_checks():
    """运行 Nox 全部检查"""
    print("[Hook] 运行 Nox 确定性检查...")
    rc, stdout, stderr = run("nox", timeout=300)

    # 保存完整输出
    (HANDOFF / "nox-output.txt").write_text(stdout + "\n" + stderr, encoding="utf-8")

    return rc == 0, stdout, stderr


# ===== 步骤 3：复制测试报告 =====

def collect_test_reports():
    """收集测试报告到 .handoff/current/"""
    print("[Hook] 收集测试报告...")
    test_results_dir = HANDOFF / "test-results"
    test_results_dir.mkdir(exist_ok=True)

    artifacts = PROJECT_ROOT / "artifacts" / "current"
    if artifacts.exists():
        for report in ["pytest.xml", "ruff.txt", "mypy.txt"]:
            src = artifacts / report
            if src.exists():
                shutil.copy2(src, test_results_dir / report)


# ===== 步骤 4：生成审查包 =====

def build_review_package(git_info, task_id):
    """生成完整审查包"""
    print("[Hook] 生成审查包...")

    # task.json
    task_json = {
        "task_id": task_id,
        "snapshot_hash": git_info["snapshot_hash"],
        "baseline_commit": git_info["baseline_commit"],
        "current_commit": git_info["current_commit"],
        "changed_files": git_info["changed_files"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # bundle.json
    bundle = {
        "task_id": task_id,
        "snapshot_hash": git_info["snapshot_hash"],
        "baseline_commit": git_info["baseline_commit"],
        "current_commit": git_info["current_commit"],
        "changed_file_count": len(git_info["changed_files"]),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    (HANDOFF / "task.json").write_text(json.dumps(task_json, indent=2, ensure_ascii=False), encoding="utf-8")
    (HANDOFF / "bundle.json").write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")

    return task_json, bundle


# ===== 步骤 5：同步到 Google Drive =====

def sync_to_drive(task_id, snapshot_hash):
    """将审查包同步到 Google Drive"""
    print("[Hook] 同步审查包到 Google Drive...")

    # 目标目录
    task_dir_name = f"{task_id}-{snapshot_hash}"
    drive_task_dir = DRIVE_PENDING / task_dir_name

    # 清理旧目录，创建新目录
    if drive_task_dir.exists():
        shutil.rmtree(drive_task_dir)
    drive_task_dir.mkdir(parents=True, exist_ok=True)

    # 复制审查包文件
    for item in HANDOFF.iterdir():
        dst = drive_task_dir / item.name
        if item.is_file():
            shutil.copy2(item, dst)
        elif item.is_dir() and item.name != "__pycache__":
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(item, dst)

    # 更新 current.json
    current_json = {
        "task_id": task_id,
        "snapshot_hash": snapshot_hash,
        "task_path": f"白槿AI桥接/pending/{task_dir_name}/",
        "status": "REVIEW_PENDING",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    current_path = DRIVE_PENDING / "current.json"
    current_path.write_text(json.dumps(current_json, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[Hook] 审查包已同步到 Google Drive: {drive_task_dir}")

    # 验证 Google Drive 桌面版是否在运行
    if not DRIVE_PENDING.exists():
        print("[Hook] ⚠️ 警告：Google Drive 目录不存在！请确认 Google Drive 桌面版正在运行。")


# ===== 步骤 6：弹窗通知 =====

def notify_user(task_id, snapshot_hash):
    """Windows 弹窗通知用户去 ChatGPT 输入'审最新任务'"""
    import ctypes
    message = (
        f"任务：{task_id}\n"
        f"快照：{snapshot_hash}\n\n"
        f"请去 ChatGPT「白槿 v2 审查中心」\n"
        f"输入：审最新任务"
    )
    ctypes.windll.user32.MessageBoxW(
        0,
        message,
        "白槿审查 — 待审任务",
        0x40  # MB_ICONINFORMATION
    )


# ===== 主流程 =====

def main():
    print("=" * 50)
    print("[Hook] 白槿 v2 Stop Hook 触发")
    print("=" * 50)

    task_id = get_task_id()
    print(f"[Hook] 当前任务: {task_id}")

    # 检查修复次数
    cycles = get_repair_cycles()
    if cycles >= MAX_REPAIR_CYCLES:
        print(f"[Hook] 修复次数已达上限 ({cycles}/{MAX_REPAIR_CYCLES})")
        notify_user(task_id, "NEEDS_HUMAN")
        result = {
            "decision": "block",
            "reason": f"自动修复已达 {MAX_REPAIR_CYCLES} 轮上限，需要人工介入。请将审查包上传到 ChatGPT 进行深度分析。"
        }
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)

    # 1. 收集 Git 证据
    git_info = collect_git_evidence()

    # 2. 运行 Nox
    nox_ok, nox_out, nox_err = run_nox_checks()
    if not nox_ok:
        print("[Hook] Nox 确定性检查失败，block")
        notify_user(task_id, git_info["snapshot_hash"])
        result = {
            "decision": "block",
            "reason": f"Nox 确定性检查未通过。请读取 .handoff/current/nox-output.txt 后继续修复。"
        }
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)

    # 3. 收集测试报告
    collect_test_reports()

    # 4. 生成审查包
    build_review_package(git_info, task_id)

    # 5. 同步到 Google Drive
    sync_to_drive(task_id, git_info["snapshot_hash"])

    # 6. 弹窗通知
    notify_user(task_id, git_info["snapshot_hash"])

    # 7. 返回 block（等待 ChatGPT 审查）
    result = {
        "decision": "block",
        "reason": f"审查包已同步到 Google Drive。请在 ChatGPT「白槿 v2 审查中心」输入"审最新任务"。审查完成后本地监听器会自动反馈结果。"
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(1)  # block —— 阻止 Claude 结束，等待审查


if __name__ == "__main__":
    main()
```

### 6.2 注册 Claude Stop Hook

在 `D:\xinbaijn\.claude\settings.json`（或创建）中：

```json
{
  "hooks": {
    "Stop": [
      {
        "command": "python D:/xinbaijn/scripts/stop_hook.py",
        "timeout": 300000
      }
    ]
  }
}
```

### 验收

```text
[ ] stop_hook.py 已创建
[ ] Python 语法无错误（python -c "import py_compile; py_compile.compile('D:/xinbaijn/scripts/stop_hook.py', doraise=True)"）
[ ] .claude/settings.json 已注册 Stop Hook
[ ] 手动运行 python D:/xinbaijn/scripts/stop_hook.py 不报错
[ ] Git 证据文件已生成到 .handoff/current/
[ ] 审查包已同步到 Google Drive pending/
[ ] current.json 已更新
[ ] Windows 弹窗已弹出
```

---

## S7：审查包同步验证（30 min）

### 目标

确认 Google Drive 同步能正常工作，尤其是：
1. 本地 → Drive 的上传速度
2. Drive → 本地（ChatGPT 写入后）的下载速度
3. 中文文件名和路径是否有问题

### 操作

1. 创建一个测试任务，运行 stop_hook.py
2. 等待 Google Drive 桌面版同步（观察任务栏图标）
3. 在 Google Drive 网页版查看 pending/ 是否出现审查包
4. 在 ChatGPT 中输入"审最新任务"
5. 观察 ChatGPT 是否成功读取文件
6. 等待 ChatGPT 写入 reviewed/
7. 观察本地是否同步到 reviewed/ 文件

### 验收

```text
[ ] 本地 → Drive 同步时间 < 30 秒
[ ] Drive → 本地同步时间 < 30 秒
[ ] 中文文件名无乱码
[ ] JSON 文件内容无编码问题
[ ] ChatGPT 能正常读取所有文件
```

---

## S8：本地监听器（1.5 h）

### 目标

自动监测 Google Drive `reviewed/` 目录，一旦出现新的 review.json：
1. 校验 JSON Schema
2. 校验 task_id 和 snapshot_hash
3. 合并到 `.handoff/current/review-input.json`
4. 运行 OPA/Conftest 裁决
5. 将结果反馈给 Claude（通过状态文件）

### 8.1 review_listener.py

创建 `D:\xinbaijn\scripts\review_listener.py`：

```python
"""
白槿 v2 审查结果监听器
监视 Google Drive reviewed/ 目录，自动处理 ChatGPT 审查结果。
"""
import json
import time
import hashlib
import shutil
from pathlib import Path
from datetime import datetime, timezone

DRIVE_REVIEWED = Path("G:/我的云端硬盘/白槿AI桥接/reviewed")
DRIVE_PENDING = Path("G:/我的云端硬盘/白槿AI桥接/pending")
PROJECT_HANDOFF = Path("D:/xinbaijn/.handoff/current")
SCHEMA_PATH = Path("D:/白槿桥接/protocol/review.schema.json")

PROCESSED_LOG = Path("D:/白槿桥接/processed_tasks.log")


def validate_schema(review_json):
    """校验 review.json 是否符合 Schema（简化版，后续可用 jsonschema 库增强）"""
    required_fields = ["task_id", "snapshot_hash", "decision", "safe_to_accept", "p0", "p1", "p2"]
    for field in required_fields:
        if field not in review_json:
            return False, f"缺少必要字段: {field}"
    if review_json["decision"] not in ["approved", "changes_required", "needs_human_review"]:
        return False, f"无效的 decision 值: {review_json['decision']}"
    return True, "OK"


def verify_identity(review_json):
    """验证 review.json 与 current.json 的 task_id 和 snapshot_hash 一致"""
    current_path = DRIVE_PENDING / "current.json"
    if not current_path.exists():
        return False, "current.json 不存在"

    current = json.loads(current_path.read_text(encoding="utf-8"))
    if review_json.get("task_id") != current.get("task_id"):
        return False, f"task_id 不匹配: {review_json.get('task_id')} vs {current.get('task_id')}"
    if review_json.get("snapshot_hash") != current.get("snapshot_hash"):
        return False, f"snapshot_hash 不匹配: {review_json.get('snapshot_hash')} vs {current.get('snapshot_hash')}"
    return True, "OK"


def merge_to_review_input(review_json):
    """将 ChatGPT 审查结果合并到 review-input.json"""
    review_input_path = PROJECT_HANDOFF / "review-input.json"

    if review_input_path.exists():
        review_input = json.loads(review_input_path.read_text(encoding="utf-8"))
    else:
        review_input = {
            "metadata": {},
            "evidence": {},
            "scope": {},
            "checks": {},
            "review": {},
            "rollback": {},
        }

    # 更新审查结果
    review_input["review"] = {
        "source": "chatgpt",
        "safe_to_accept": review_json.get("safe_to_accept", False),
        "p0": len(review_json.get("p0", [])),
        "p1": len(review_json.get("p1", [])),
        "p2": len(review_json.get("p2", [])),
        "missing_tests": len(review_json.get("missing_tests", [])),
        "out_of_scope_changes": review_json.get("out_of_scope_changes", []),
        "requires_human_review": review_json.get("requires_human_review", False),
    }

    review_input_path.write_text(json.dumps(review_input, indent=2, ensure_ascii=False), encoding="utf-8")

    return review_input


def get_decision(review_json):
    """根据审查结果返回 block/allow/needs_human"""
    if review_json.get("requires_human_review"):
        return "needs_human"
    if review_json.get("safe_to_accept") and len(review_json.get("p0", [])) == 0 and len(review_json.get("p1", [])) == 0:
        return "allow"
    return "block"


def write_feedback(review_json, decision):
    """将反馈写入 Claude 可读的状态文件"""
    p0_list = review_json.get("p0", [])
    p1_list = review_json.get("p1", [])
    p2_list = review_json.get("p2", [])
    missing = review_json.get("missing_tests", [])

    feedback = f"""# ChatGPT 审查反馈

## 结论：{review_json.get('summary', '无')}

## P0 缺陷（{len(p0_list)}个）
"""
    for item in p0_list:
        feedback += f"- **{item['file']}:{item.get('line', '?')}** — {item['problem']}\n  修复：{item['required_fix']}\n"

    feedback += f"\n## P1 缺陷（{len(p1_list)}个）\n"
    for item in p1_list:
        feedback += f"- **{item['file']}:{item.get('line', '?')}** — {item['problem']}\n  修复：{item['required_fix']}\n"

    feedback += f"\n## P2 建议（{len(p2_list)}个）\n"
    for item in p2_list:
        feedback += f"- **{item['file']}:{item.get('line', '?')}** — {item['problem']}\n"

    if missing:
        feedback += f"\n## 缺失测试\n"
        for m in missing:
            feedback += f"- {m}\n"

    feedback += f"\n## 裁决：{'✅ 通过' if decision == 'allow' else '❌ 需要修改' if decision == 'block' else '⚠️ 需要人工确认'}"

    # 写入 Claude 可读的反馈文件
    feedback_path = PROJECT_HANDOFF / "chatgpt-feedback.md"
    feedback_path.write_text(feedback, encoding="utf-8")


def mark_processed(review_path):
    """记录已处理的审查结果，避免重复处理"""
    processed_log = f"{review_path.name} | {datetime.now(timezone.utc).isoformat()}\n"
    with open(PROCESSED_LOG, "a", encoding="utf-8") as f:
        f.write(processed_log)


def is_already_processed(review_path):
    """检查是否已处理过"""
    if not PROCESSED_LOG.exists():
        return False
    content = PROCESSED_LOG.read_text(encoding="utf-8")
    return review_path.name in content


def scan_and_process():
    """扫描 reviewed/ 目录，处理新的审查结果"""
    if not DRIVE_REVIEWED.exists():
        print(f"[监听器] reviewed/ 目录不存在: {DRIVE_REVIEWED}")
        return

    review_files = sorted(DRIVE_REVIEWED.glob("*-review.json"), key=lambda f: f.stat().st_mtime)
    if not review_files:
        return

    for review_path in review_files:
        if is_already_processed(review_path):
            continue

        print(f"[监听器] 发现新审查结果: {review_path.name}")

        try:
            review_json = json.loads(review_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"[监听器] JSON 解析失败: {e}")
            continue

        # 1. Schema 校验
        valid, msg = validate_schema(review_json)
        if not valid:
            print(f"[监听器] Schema 校验失败: {msg}")
            continue

        # 2. identity 校验
        valid, msg = verify_identity(review_json)
        if not valid:
            print(f"[监听器] 身份校验失败: {msg}")
            continue

        # 3. 合并到 review-input.json
        merge_to_review_input(review_json)

        # 4. 裁决
        decision = get_decision(review_json)

        # 5. 写入反馈
        write_feedback(review_json, decision)

        # 6. 标记已处理
        mark_processed(review_path)

        print(f"[监听器] 审查结果已处理: {review_path.name} → {decision}")


def main():
    print("[监听器] 白槿 v2 审查结果监听器启动")
    print(f"[监听器] 监视目录: {DRIVE_REVIEWED}")

    while True:
        try:
            scan_and_process()
        except Exception as e:
            print(f"[监听器] 错误: {e}")

        time.sleep(5)  # 每 5 秒扫描一次


if __name__ == "__main__":
    main()
```

### 8.2 使用方式

```bash
# 在后台运行监听器（或开机自启）
python D:/xinbaijn/scripts/review_listener.py
```

### 验收

```text
[ ] review_listener.py 已创建
[ ] 手动在 reviewed/ 放入一个测试 review.json → 监听器检测到
[ ] Schema 校验正常工作（缺少字段 → 拒绝）
[ ] identity 校验正常工作（hash 不匹配 → 拒绝）
[ ] 合并后的 review-input.json 内容正确
[ ] chatgpt-feedback.md 生成正确
[ ] 重复文件不会被重复处理
```

---

## S9：端到端测试（2 h）

### 目标

用 3 个低风险真实任务走通完整闭环。

### 测试任务

```
测试 A：文档修复（修改 README.md 一句话）
测试 B：纯函数（修改一个纯函数 + 增加测试）
测试 C：小 Bug 修复（不涉及数据库/认证/公共接口）
```

### 每个任务的测试流程

```
1. Claude 完成修改
2. Claude 准备结束 → Stop Hook 触发
3. 检查 .handoff/current/ 是否生成全部文件
4. 检查 Google Drive pending/ 是否同步
5. 弹窗通知是否出现
6. 去 ChatGPT 输入"审最新任务"
7. 等待 ChatGPT 审查并写入 reviewed/
8. 检查本地是否同步到 review.json
9. 监听器是否检测到并处理
10. chatgpt-feedback.md 是否正确
11. Claude 是否根据反馈修复（如需要）
```

### 验收

```text
[ ] 测试 A 完整链路通（文档修复 → 审查 → ACCEPTED）
[ ] 测试 B 完整链路通（纯函数 → 审查 → ACCEPTED）
[ ] 测试 C 完整链路通（Bug 修复 → 审查 → ACCEPTED）
[ ] 故意制造一个 P1（删除受保护测试）→ ChatGPT 拒绝 → Claude 修复 → 重审通过
[ ] 两轮修复超限 → NEEDS_HUMAN_REVIEW
[ ] 整个流程中不需要手动复制粘贴任何内容（只需输入"审最新任务"）
```

---

## S10：稳定运行 + 持续调优

### 优化项

```text
[ ] 将 ChatGPT Google Drive App 权限设为"从不询问"（如果可用）
[ ] 将 review_listener.py 设为开机自启
[ ] 审查包文件过大时压缩 patch.diff（截断超大 diff）
[ ] 记录每次审查的耗时（优化 prompt 和文件大小）
[ ] 监控 Google Drive 同步延迟
```

---

# 五、关键风险与应对

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| ChatGPT Google Drive App 权限不支持"从不询问" | 中 | 低 | 每次点一下"允许"，多 1 秒 |
| Google Drive 同步延迟 > 60 秒 | 低 | 中 | 监听器每 5 秒轮询，延迟可控 |
| ChatGPT 审查输出不符合 Schema | 中 | 中 | 监听器校验 + 项目指令中强调格式 |
| GPT-5.5 审查消耗额度过多 | 中 | 中 | 小任务批量合并审查；文档/注释类跳过 ChatGPT |
| ChatGPT 审查耗时过长（> 5 分钟） | 低 | 中 | 限制审查包大小；简化 prompt |
| Google Drive 桌面版未运行 | 低 | 高 | stop_hook.py 检测并提醒 |

---

# 六、日常使用流程（最终形态）

```
你在 Claude Code 下达任务
    │
    ▼
Claude 写代码、测试、改 bug
    │
    ▼
Claude 说"完成了"
    │
    ▼
┌─ Stop Hook 自动触发 ──────────────────┐
│  Git 证据 → Nox 检查 → 审查包 → Drive │
└───────────────────────────────────────┘
    │
    ▼
桌面弹窗："TASK-008 待审"
    │
    ▼
你切换到 ChatGPT，输入：审最新任务
    │
    ▼
ChatGPT 读 Drive → 审查 → 写回 Drive
    │
    ▼
Google Drive 同步回本地
    │
    ▼
监听器自动处理 → OPA 裁决 → 反馈 Claude
    │
┌───┴───┐
│ 通过   │ 不通过
▼       ▼
任务完成  Claude 修复 → 下一轮
         (最多 2 轮)
```

**你每轮只需要做一件事：输入"审最新任务"。**

---

# 七、总验收清单

## S0-S5：基础设施

```text
[ ] Google Drive App 已连接
[ ] 白槿AI桥接/ 目录结构完整
[ ] ChatGPT "白槿 v2 审查中心" 项目已创建
[ ] bridge-test 连通测试通过
[ ] Google Drive 桌面版已安装，同步正常
[ ] 审查协议.md 和 review.schema.json 已上传
```

## S6-S8：自动化

```text
[ ] stop_hook.py 正常运行
[ ] .claude/settings.json Hook 已注册
[ ] Git 证据收集完整（5 个文件 + changed/）
[ ] Nox 检查在 Hook 中正常运行
[ ] 审查包同步到 Google Drive 正常
[ ] Windows 弹窗正常弹出
[ ] review_listener.py 正常运行
[ ] Schema 校验正常
[ ] review-input.json 合并正常
[ ] chatgpt-feedback.md 生成正常
```

## S9：端到端

```text
[ ] 3 个低风险任务全部走通
[ ] ChatGPT 审查结果正确
[ ] 整个流程无需复制粘贴
[ ] 2 轮修复上限生效
```

---

# 八、与治理文档的对齐

本方案完全遵循治理文档体系：

| 治理要求 | 本方案实现 |
|---|---|
| 需求明确 | task.json 包含 goal / allowed_paths / forbidden |
| 证据完整 | Git + changed/ + test-results/ |
| 测试真实 | Nox 确定性检查（pytest / Ruff / mypy / Import Linter） |
| 审查独立 | ChatGPT 独立审查，不采信 Claude 结论 |
| 政策通过 | OPA/Conftest 读取 review-input.json 裁决 |
| 回滚成功 | baseline tag + Git 恢复点（在 stop_hook.py 中自动建立） |
| Codex CLI 只读审查 | → 替换为 ChatGPT（Google Drive 桥接），同样只读、结构化 |
| 权力分离 | Claude 只写业务代码，ChatGPT 只读审查，policy 在隔离目录 |
| 小批次 | 每个任务保持 ≤ 500 行、≤ 8 个文件 |
| 最多 2 轮修复 | stop_hook.py 中硬编码 MAX_REPAIR_CYCLES = 2 |
