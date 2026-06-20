# 白槿 v2 Phase 完成政策

> 目的：明确“什么时候一个 Phase 才算真正完成”，阻止以文件数量、代码行数、测试数量或 AI 口头说明代替正式验收。

---

## 一、核心定义

Phase 完成不是：

- 文件全部创建；
- 功能暂时运行；
- pytest 全绿；
- Claude 宣称完成；
- Codex 没有提出明显问题；
- 进度表写成 100%。

Phase 完成必须是：

```text
范围内所有任务均被正式验收
+
Phase 级系统验证通过
+
无未处理阻断缺陷
+
回滚和恢复能力已验证
```

---

## 二、任务完成条件

每个任务必须同时满足：

1. `task.md`、`acceptance.yaml`、`risk.yaml` 完整；
2. 修改范围没有越界；
3. 所有 required checks 实际运行；
4. P0 = 0；
5. P1 = 0；
6. Codex `safe_to_accept = true`；
7. OPA/Conftest 无拒绝项；
8. 需要人工确认的事项已明确批准；
9. 恢复点存在；
10. 回滚演练通过；
11. 最终报告绑定当前 commit；
12. 状态为 `ACCEPTED`。

---

## 三、Phase 级完成条件

Phase 必须满足：

### 3.1 范围完整

- Phase 计划中的任务全部有唯一任务编号；
- 没有“以后再补”但未登记的范围；
- 所有删减项均有批准记录；
- Phase 的非目标明确。

### 3.2 架构完整

- Import Linter 合同全部通过；
- 没有未经批准的跨层依赖；
- 公共接口有合同测试；
- 依赖方向与设计文档一致；
- 不存在已知真实循环依赖。

### 3.3 行为完整

- 单元测试通过；
- 合同测试通过；
- 集成测试通过；
- Phase 验收测试通过；
- 关键失败路径经过故障注入；
- 测试数量和受保护测试未异常下降。

### 3.4 安全完整

- Gitleaks 通过；
- 高严重性依赖漏洞已处理；
- Semgrep/OpenGrep 无未处理严重问题；
- 日志和配置没有泄露密钥；
- 权限和认证变化经过人工复核。

### 3.5 审查完整

- 每个任务均有独立 Codex 审查；
- Phase 级汇总审查通过；
- P0 = 0；
- P1 = 0；
- P2 已登记并有处理计划；
- 所有审查报告绑定对应 commit。

### 3.6 数据与恢复完整

- 所有迁移均在副本上演练；
- 数据备份可恢复；
- Phase 级回滚或前向修复方案存在；
- 至少一次 Phase 恢复演练通过；
- 恢复后的烟雾测试通过。

### 3.7 治理完整

- OPA 政策通过；
- 没有未经批准的治理规则修改；
- 所有例外均有编号和到期时间；
- 没有已过期例外；
- 所有人工批准都有记录。

---

## 四、禁止进入下一 Phase 的条件

任一条件成立即禁止：

- 任一 P0/P1 未关闭；
- 关键测试被 skip；
- 受保护测试被删除；
- 架构合同失败；
- 报告与 commit 不匹配；
- Codex 拒绝；
- OPA 拒绝；
- 回滚失败；
- 数据迁移未演练；
- 例外已过期；
- 治理政策被未经批准修改；
- 负责人无法理解最终风险。

---

## 五、完成状态

Phase 状态：

```text
DRAFT
READY
IN_PROGRESS
VERIFYING
BLOCKED
NEEDS_HUMAN_REVIEW
ACCEPTED
RELEASED
```

`ACCEPTED` 只能由聚合程序根据所有任务证据生成。

`RELEASED` 只能在发布后烟雾测试和恢复验证通过后设置。

---

## 六、证据保留

Phase 完成必须归档：

```text
phase-package/
├── phase-plan.md
├── task-index.yaml
├── architecture-results/
├── test-results/
├── security-results/
├── task-reviews/
├── phase-review.json
├── policy-result.json
├── rollback-report.md
├── exceptions.yaml
└── final-phase-report.md
```

---

## 七、最终原则

```text
代码写完
≠
任务完成

所有测试绿灯
≠
Phase 完成

只有完整证据链与恢复能力通过
=
Phase 完成
```
