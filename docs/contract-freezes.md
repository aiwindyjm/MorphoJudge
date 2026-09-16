# 契约冻结点

本文件是 MorphoJudge 契约的唯一冻结登记处。冻结条目只能向后兼容地新增字段；删除字段或改变语义必须先提交 Architecture Change Request（见文末）。批次施工中新增冻结项时，必须在本文件登记后再实现代码。

## Freeze 0：任务开始前

冻结 PRD 0.21 的术语、状态、事实/模型边界和当前 Web 原型视觉交互。

## Freeze 1：V0.1.0-alpha（Batch-01 / ARC-001 + DATA-001）

冻结 `SnapshotIdentity`、`SelectionDecision`、`CoverageSummary`、`MapNode`、`MapEdge`、`EvidenceAnchor`、`Finding`、`Explanation`、`Review` 和 `AnalysisSession`。字段新增只能向后兼容；删除或改语义必须提交 ACR。

Batch-02 追加（向后兼容，无需 ACR）：`MapNode.note`（仅承载确定性来源标注如 `feature_mapping:human`，禁止模型文本）与 `SoftwareMap {snapshot_id, nodes, edges, truncated, limits}` 容器契约；行为目标合成节点（`data:*`/`svc:*`/`unknown:*`）的 resolution 语义：unknown 前缀=unresolved，动态构造=candidate，静态可证=resolved。

Batch-02 审计修复追加（向后兼容，无需 ACR）：关系边来源不可丢失——`MapEdge.file_path`（repo 相对路径，指向调用/引用点所在文件）与 `MapEdge.line`（1-based 行号；类型引用边指向参数/返回注解所在行，manifest 页面级事实允许为 null）；`SoftwareMap.file_reports: [FileParseReport {path, status, issue_count, note}]` 使每条边可回溯其来源文件的解析状态；`ParseStatus {parsed|parsed_with_errors|limited|error}` 上移为契约枚举。每条边的完整追溯链为：`edge.file_path + line → SoftwareMap.file_reports[path].status → edge.snapshot_id`。

兼容边界（B02-R2-03 登记）：上述追加使契约版本递增到 `1.1.0`（minor，无破坏性变更）。`MapEdge.file_path/line` 在 Schema 中保持可选，目的是**允许 v1.0.0 旧输入继续通过校验**；MorphoJudge 自产的新分析产物（v1.1.0 起）必须始终携带来源定位，该完整性由 pipeline 测试强制，而非由 Schema 拒绝旧输入实现。

本批同时冻结以下横切语义（ARC-001）：

### F1.1 分层依赖（单向数据流）

```text
Repository/Snapshot → Selection/Coverage → Parser → IR → Rules → Evidence → Finding
                                                          → Explanation（只读证据，按需）
Persistence 只写入上游产物；API 只读写 Persistence；Web 只调用 API。
```

- 上层可以消费下层产物，禁止反向依赖：Parser 不得调用 API，Rules 不得读取未经过 Selection 的任意路径，Explanation 不得修改 IR/Finding。
- 预览（preview）逻辑必须与实际分析共用同一确定性函数（首先是 Selection）。

### F1.2 统一阶段状态

- 分析级状态（AnalysisSession.status）：`queued`、`running`、`completed`、`completed_with_limits`、`failed`、`cancelled`。
- 阶段级状态（StageRecord.status）：`queued`、`running`、`completed`、`failed`、`skipped`。
- `completed_with_limits` 表示确定性主流程完成但存在覆盖限制（limited/failed/unresolved），不得伪装为 `completed`，也不得显示为安全。
- 状态字符串为 ASCII 小写，两端（Pydantic/TypeScript）枚举值必须逐字一致。

### F1.3 统一错误对象

```json
{
  "schema_version": "1.0.0",
  "error": { "code": "REF_NOT_FOUND", "message": "...", "retryable": false, "details": {} }
}
```

- `code`：ASCII 大写蛇形稳定枚举（见 `packages/contracts/schema.json` 的 `ErrorCode`）。Batch-01 起始集合：`REPOSITORY_NOT_FOUND`、`NOT_A_GIT_REPOSITORY`、`LINKED_WORKTREE_NOT_SUPPORTED`、`REF_NOT_FOUND`、`PATH_OUT_OF_ROOTS`、`PATH_TRAVERSAL_DETECTED`、`SYMLINK_ESCAPE`、`GIT_COMMAND_FAILED`、`INVALID_INPUT`、`INTERNAL_ERROR`。
- `message`：面向用户的可读说明，不得包含密钥、绝对宿主路径以外的敏感数据；`details` 是结构化补充（dict），可为空对象。
- `retryable`：调用方是否可原样重试。新增 code 只能追加，不能改写旧 code 的语义。

### F1.4 schema_version

- 全局契约版本常量随 `packages/contracts/schema.json` 顶层 `schema_version` 字段发布。版本历史：`1.0.0` = Batch-01 首发；`1.1.0` = Batch-02 累计向后兼容追加（MapNode.note、SoftwareMap、MapEdge.file_path/line、FileParseReport、ParseStatus）。
- 语义：向后兼容新增字段 → minor 递增；删除/改语义 → major 递增且必须提交 ACR。JSON Schema 由 Pydantic 模型生成，TS 类型与其保持字段/枚举一致；所有本地 `$ref` 必须可在根 Schema 解析（生成器含同名冲突不变量与引用完整性测试）。

### F1.5 ID 与可复现语义

- 所有 ID 均为 string。`repository_id = sha256(canonical_path)`（小写 hex，64 位）；`snapshot_id = sha256("morphojudge-snapshot-v1\0" + repository_id + "\0" + base_commit + "\0" + target_commit + "\0" + rules_version)`。
- 同输入必须产出同 ID；不同 commit 必须产出不同 snapshot_id。commit 一律为 40 位小写 hex。
- 未跟踪（untracked）文件策略：commit-to-commit 分析范围不包含工作区未跟踪文件；它们只在 Snapshot 报告中以信息性 `working_tree` 记录出现，永不静默忽略，也永不进入 Diff 文件清单。
- 脏工作区（dirty workspace）：以 `workspace_dirty` 布尔信息位记录，不改变 snapshot_id（快照身份绑定的是提交对象），不执行任何 hook。
- 子模块与 LFS：检测到即记录为限制（limitation note），不自动拉取、不联网。

### F1.6 变更入口

任何对上述冻结语义的修改必须先提交 `docs/architecture-change-requests/ACR-YYYYMMDD-NNN.md`，经 Codex 复核后方可合并；施工 AI 不得在批次内直接改写。

## Freeze 2：V0.1.3-alpha

冻结 Evidence schema、resolver 状态、行号语义、关系类型和 Finding→Evidence 关联。规则只能新增 rule_id，不能重用旧 ID。

## Freeze 3：V0.1.4-alpha

冻结 SQLite 表、外键、索引、迁移版本和分析状态机。旧快照不可被新运行覆盖。

## Freeze 4：V0.1.5-alpha

冻结 HTTP request/response/error schema、分页、过滤、幂等键和取消语义。Web 只能适配，不得自行改变 API 事实。

## Freeze 5：V0.1.6-alpha

冻结 Provider 接口、EvidenceContext、Explanation schema、引用校验和远程授权记录。模型输出永远不能改变确定性图。

## Change Request

文件：`docs/architecture-change-requests/ACR-YYYYMMDD-NNN.md`。必须包含问题、当前契约、候选方案、推荐方案、受影响任务、迁移、测试和回滚方式。未获 Codex 复核前不得合并。
