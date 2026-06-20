# 白槿 v2 Claude Code、Codex CLI 与 ChatGPT 协同开发审查方案

> 目标：明确三类 AI 的职责、权限、输入输出和交接机制，避免同一 AI 同时开发、测试、审查并批准自己的工作。

---

## 一、角色定位

### Claude Code：受约束的开发者

负责：

- 阅读批准后的任务；
- 在允许路径内修改代码；
- 添加普通测试；
- 运行统一检查；
- 根据结构化缺陷进行修复；
- 输出开发说明。

禁止：

- 修改治理政策；
- 修改受保护测试；
- 删除失败测试；
- 修改验收标准；
- 自行设置 `ACCEPTED`；
- 修改 Codex 审查结果；
- 直接批准高风险变更。

### Codex CLI：只读独立审查者

负责：

- 审查 Git 变更；
- 结合完整文件理解上下文；
- 检查任务与实现是否一致；
- 发现设计、并发、生命周期和回滚问题；
- 识别测试不足；
- 输出结构化审查结果。

禁止：

- 在审查阶段修改候选代码；
- 修改 OPA 政策；
- 单独决定任务完成；
- 用自然语言“看起来没问题”代替结构化证据。

### ChatGPT：复杂复核与治理顾问

负责：

- 审查难以自动判断的设计；
- 复核 Phase 级风险；
- 评估治理规则是否合理；
- 分析审查包；
- 帮助项目负责人理解报告；
- 对数据库、权限、隐私和不可逆变更提出二次意见。

ChatGPT 不作为本地自动门禁的必要运行依赖。网络不可用时，本地开源工具和 OPA 仍必须能够阻断错误。

---

## 二、协同链路

```text
项目负责人冻结任务
↓
Claude Code 开发
↓
Git 收集真实变化
↓
Nox 运行确定性检查
↓
OPA 初次裁决
↓
Codex CLI 只读审查
↓
OPA 最终裁决
↓
项目负责人查看 final-report.md
↓
高风险变更上传审查包给 ChatGPT/真人复核
```

---

## 三、统一交接包

```text
review-package/
├── task.md
├── acceptance.yaml
├── risk.yaml
├── status.yaml
├── changed-files.txt
├── patch.diff
├── changed/
├── test-results/
├── security-results/
├── review-input.json
├── codex-review.json
├── policy-result.json
├── rollback-report.md
└── final-report.md
```

Claude、Codex、ChatGPT 和真人审查者都以同一个审查包为依据，避免各自看到不同版本。

---

## 四、结构化审查协议

Codex 必须输出：

```json
{
  "task_id": "TASK-ID",
  "decision": "approve|changes_required|human_review",
  "safe_to_accept": false,
  "p0": [],
  "p1": [],
  "p2": [],
  "missing_tests": [],
  "out_of_scope_changes": [],
  "policy_violations": [],
  "requires_human_review": false
}
```

任何字段缺失、JSON 解析失败或 commit 不匹配，均视为审查失败。

---

## 五、自动修复限制

```text
第一次 Codex 拒绝
→ Claude 修复
→ 全量重跑

第二次 Codex 拒绝
→ Claude 最后一次修复
→ 全量重跑

仍未通过
→ NEEDS_HUMAN_REVIEW
```

禁止无限循环。

每次修复必须重新生成全部证据，不得复用旧测试结果。

---

## 六、ChatGPT 复核触发条件

出现以下情况时，将审查包上传给 ChatGPT 或真人专家：

- 数据库迁移；
- 用户数据删除；
- 身份认证和权限；
- 密钥管理；
- 治理政策修改；
- 新的公共接口；
- 跨层架构变化；
- 大规模重构；
- P0/P1 争议；
- 两轮 AI 修复仍未通过；
- 回滚方案不确定；
- 审查工具之间结论冲突。

---

## 七、网络与成本策略

第一层必须本地运行：

- Git；
- Nox；
- Ruff；
- mypy；
- Import Linter；
- pytest；
- Gitleaks；
- OPA/Conftest；
- Codex CLI（按当前账户能力使用）。

ChatGPT 当前对话不应成为自动化流水线的硬依赖。高风险任务通过人工上传单个 ZIP 完成复核。

---

## 八、最终原则

```text
Claude 负责产生候选改动
Codex 负责提出独立反对意见
开源工具负责确定性证据
OPA 负责政策裁决
项目负责人负责风险选择
ChatGPT/真人负责复杂二次复核
```

任何 AI 都不是可信根，可信根来自权限隔离、结构化证据、机器政策和可验证回滚。
