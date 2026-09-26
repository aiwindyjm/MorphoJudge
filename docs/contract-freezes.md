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

- `code`：ASCII 大写蛇形稳定枚举（见 `packages/contracts/schema.json` 的 `ErrorCode`）。Batch-01 起始集合：`REPOSITORY_NOT_FOUND`、`NOT_A_GIT_REPOSITORY`、`LINKED_WORKTREE_NOT_SUPPORTED`、`REF_NOT_FOUND`、`PATH_OUT_OF_ROOTS`、`PATH_TRAVERSAL_DETECTED`、`SYMLINK_ESCAPE`、`GIT_COMMAND_FAILED`、`INVALID_INPUT`、`INTERNAL_ERROR`。Batch-04 追加（兼容新增，不改旧 code 语义）：`ANALYSIS_NOT_FOUND`（404）、`ANALYSIS_NOT_READY`（409，产物未提交时查询结果）、`ANALYSIS_CONFLICT`（409，幂等键复用于不同请求或对终态分析取消/重跑）、`REPOSITORY_NOT_REGISTERED`（404，API 只接受登记仓库 ID）、`FINDING_NOT_FOUND`（404）、`EVIDENCE_NOT_FOUND`（404）。Batch-04-R1 追加：`ROUTE_NOT_FOUND`（404，未知路由/方法统一错误 envelope）。
- `message`：面向用户的可读说明，不得包含密钥、绝对宿主路径以外的敏感数据；`details` 是结构化补充（dict），可为空对象。
- `retryable`：调用方是否可原样重试。新增 code 只能追加，不能改写旧 code 的语义。

### F1.4 schema_version

- 全局契约版本常量随 `packages/contracts/schema.json` 顶层 `schema_version` 字段发布。版本历史：`1.0.0` = Batch-01 首发；`1.1.0` = Batch-02 累计向后兼容追加（MapNode.note、SoftwareMap、MapEdge.file_path/line、FileParseReport、ParseStatus）；`1.2.0` = Batch-04 兼容追加（ErrorCode 新增 6 个 API 错误码，无字段删除或语义变更）；`1.3.0` = Batch-04-R1 兼容追加（API 传输 DTO 进入公开 Schema/TS 契约与漂移验证、`ROUTE_NOT_FOUND`、`AnalysisOptions`，见 Freeze 4 R1 登记）。`1.4.0` = Batch-06 兼容追加（explain 端点 DTO 与 `EXPLAIN_*` 错误码，见 Freeze 4/5 Batch-06 登记）。
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

### Freeze 3 登记（Batch-04 / DB-001）

- 迁移登记：`schema_migrations(version, name, applied_at)`，每个迁移在**显式事务**（BEGIN IMMEDIATE…COMMIT，覆盖 DDL/DML/版本登记）内应用；中途失败整体回滚并保留旧库；比当前构建更新的 schema 版本拒绝写入。当前版本 2（`0001_initial`、`0002_integrity_and_manifest`：reviews 复合外键重建 + analyses 增列）。
- 表集合：`repositories`（仓库登记，repository_id = sha256(canonical_path)，按 F1.5）、`analyses`（分析会话：`idempotency_key` 唯一、`request_hash`、`status`、`resumable`、`cancel_requested`、`failure_reason`、`snapshot_id`、`manifest_status`、`manifest_digest`、`options_json`）、`stages`（阶段记录，(analysis_id, stage) 主键，stage 取值即 F1.2 冻结枚举）、`stage_outputs`（阶段 checkpoint 输出 JSON，恢复入口）、`snapshots`（SnapshotReport 缓存，INSERT OR IGNORE，不可变）、`analysis_results`（每分析完整结果文档 JSON）、`maps`（base/target 两侧 SoftwareMap）、`decisions`（SelectionDecision 行）、`findings`（按 analysis_id 命名空间，`evidence_ids_json` + 索引 snapshot/category/rule）、`evidence`（EvidenceAnchor 行）、`reviews`（人工复核，(analysis_id, finding_id) 主键 + 指向 findings 的复合外键）。
- 引用完整性（R1）：review→finding 由复合外键强制；finding→evidence 引用在 report 事务内校验存在性，悬空引用拒绝写入且事务回滚。
- 不覆盖语义：findings/evidence/maps/decisions 均以 `analysis_id` 为命名空间，重跑同一 snapshot 产生新 analysis，不改写旧行；`snapshots` 只插入不更新；终态分析（completed*/failed/cancelled）不被迟到状态迁移改写（CAS）。
- 分析状态机：`queued → running → completed | completed_with_limits | failed | cancelled`；`cancelled` 为终态；失败保留已完成阶段、关闭 running 阶段并保存 failure_reason（脱敏：绝对路径替换为 `<path>`）；`running` 中断的分析在 daemon 重启时从最后 checkpoint 恢复。
- 事务边界（R1 修正为四 checkpoint）：worker 的 git / parse（选择+解析+两侧图）/ rules（行为+依赖+一致性+证据+finding+影响）/ report 各自单事务提交；report 事务包含 findings+evidence+analysis_results+终态；取消与终态在同一 report 事务内线性化（取消标记先落库则取消获胜，产物保留）。

## Freeze 4：V0.1.5-alpha

冻结 HTTP request/response/error schema、分页、过滤、幂等键和取消语义。Web 只能适配，不得自行改变 API 事实。

### Freeze 4 登记（Batch-04 / API-001 + API-002；Batch-04-R1 修正）

> R1 变更记录：独立审计（B04-R1-04/05/06）判定 Batch-04 原登记的"结果仅终态可读"语义收缩了原始规格、"map 为正式端点"偏离指定 URL、响应缺 schema_version、`/repositories` 暴露内部 canonical_path。以下登记为按修复指令（zcode-fix-prompt.md）纠正后的版本；纠正理由与原始偏差见 PRIVATE 审计记录。

- 端点：`POST /v1/analyses`（创建，请求体 `{repository_id, base_ref, target_ref, rules_version?, options?}`，未知字段拒绝；只接受登记 repository_id，不接受路径/命令）；`GET /v1/analyses/{id}`（状态+阶段）；`POST /v1/analyses/{id}/cancel`；`GET /v1/analyses/{id}/coverage`（选择决策分页 + 全阶段 stage_coverage + limits + manifest 状态）；`GET /v1/analyses/{id}/software-map?side=base|target`（正式端点；`/map` 为 Batch-04 兼容别名）；`GET /v1/analyses/{id}/impact-paths`；`GET /v1/analyses/{id}/summary`（输入身份、diff 文件清单、阶段覆盖、限制、证据定位失败、manifest 绑定）；`GET /v1/analyses/{id}/findings`（分页+过滤）；`GET /v1/analyses/{id}/findings/{finding_id}`（含证据锚点）；`GET /v1/analyses/{id}/evidence/{evidence_id}`；`GET|POST /v1/analyses/{id}/reviews`；`GET /v1/repositories`（仅 repository_id/name/registered_at，不含内部路径）。
- 响应包络（R1）：所有响应 DTO 携带 `schema_version`；分页响应 `{items, total, limit, offset}`（findings 按 finding_id、decisions 按 path 稳定排序）；结果响应携带 `availability {analysis_status, artifacts: complete|partial, note}`。
- 结果就绪语义（R1 登记；**R2 按已验实现纠正分层**——R1 文本把 findings/evidence/reviews 归入 rules checkpoint 是登记错误，实际实现及交付一直按三层产物读取，本登记纠正为与实现一致，不新增查询能力）：按**已提交产物**判定——map 端点与 coverage 最低需要 parse checkpoint（maps 行/选择决策；coverage 的 stage_coverage/limits 全量信息在 rules checkpoint 提交后补充）；summary/impact-paths 需要 rules checkpoint 文档；findings/evidence/reviews 需要 report 事务提交的行产物。产物存在即可读（含 failed/cancelled 分析的已提交产物，availability 标 partial）；无产物返回 409 `ANALYSIS_NOT_READY`（details 携带 status 与缺失项），绝不返回可被误读为"无发现/安全"的空集合。
- 选项契约（R1）：`options {impact_max_depth: 1..50=10, impact_max_nodes: 1..5000=1000}` 是本批唯一支持的选项集；options 进入幂等键派生与 `analyses.options_json`，是恢复输入的一部分。其余 options 未提供、未实现。
- 幂等键：可选请求头 `Idempotency-Key`（1..200 字符，无控制字符）；缺省时由服务端从请求内容确定性派生（sha256(canonical payload)）。同键同请求 → 返回既有分析（200，响应头 `Idempotency-Replayed: true`）；同键不同请求 → 409 `ANALYSIS_CONFLICT`。`analysis_id = "analysis:" + sha256(idempotency_key)[:24]`。
- 取消语义：对非终态分析设置协作式取消标记；尚未开始执行的排队任务直接转 `cancelled`；已开始任务在阶段边界停止，且取消与终态在 report 事务内线性化（标记先落库则取消获胜、产物保留）。对终态分析取消 → 409 `ANALYSIS_CONFLICT`。
- 错误隐私（R1；R2 补充）：校验错误只返回字段位置与错误类型，不回显 input/ctx 原文；未知路由/方法返回统一错误 envelope（`ROUTE_NOT_FOUND`/404）；`/repositories` 不输出仓库内部 canonical_path；worker failure_reason 经脱敏（绝对路径 → `<path>`，长度截断）。R2 补充：coverage `status` 与 findings `category` 等手工查询参数校验同样不回显原值（INVALID_INPUT + 字段位置/原因）。

### Freeze 4 R2 登记（Batch-04-R2）

- 旧库迁移（B04-R2-01）：v1→v2 迁移在同一事务内先检测悬空 review（引用不存在 finding）；存在则显式拒绝并整体回滚（旧表/版本/字段原样保留，错误只含数量不输出 note 原文），不自动删除人工记录。已在旧版迁移中被删除的行无法恢复。执行所有权（R2-02）：单进程范围内同一 (数据库, analysis_id) 只有一个执行者——进程级执行声明，run 生命周期持有、finally 释放、重启后为空不影响恢复；非持有者不产生任何写入。取消（R2-02/03）：取消是否接受由数据库条件写入裁决——取消标记先于 report 提交落库则取消生效（产物保留）；report 先提交终态则取消返回 409 且不改 cancel_requested/updated_at/任何阶段；排队直接取消走同一终态 CAS。取消生效时把仍为 queued/running 的阶段关闭为 skipped 并注明 `cancelled_by_user` 与完成时间（不新增公开枚举）；CAS 未成功不得改写任何行。manifest 深层校验（R2-04）：`human_feature_mapping` 每项必须为 dict 且含字符串 `page`/`feature_id`/`feature`、可选 `events`（字符串列表）、`confirmed_by == "human"`；`required_entities.permission_modules` 每项必须为字符串；违反者进入 `schema_invalid`（digest 保留，分析继续并携带 `manifest:schema_invalid` 限制）；未消费的描述性元数据不受限。

### Freeze 4 Batch-06 登记（explain 端点，兼容追加）

- 端点：`POST /v1/analyses/{id}/explain`（请求体 `{subject_type: finding|node, subject_id, evidence_ids?, provider: fake|ollama|remote, remote_consent?{endpoint, acknowledged}}`；同步执行，200 返回 completed|failed 的 ExplanationPayload）；`GET /v1/analyses/{id}/explain?subject_type&subject_id`（列表）；`GET /v1/analyses/{id}/explain/providers`；`GET /v1/analyses/{id}/explain/authorizations`（远程授权审计）。
- 错误码追加：`EXPLAIN_SUBJECT_NOT_FOUND`（404）、`EXPLAIN_EVIDENCE_NOT_FOUND`（400，空证据/幻觉/跨分析引用）、`EXPLAIN_PROVIDER_UNAVAILABLE`（503，本地 Ollama 未配置/不可达/模型缺失）、`EXPLAIN_CONSENT_REQUIRED`（403，远程未授权或端点非法）。Provider 失败不自动切换。
- 契约版本 1.4.0：explain DTO（ExplainRequest/ExplainConsentInput/ExplainClaimItem/ExplanationPayload/ExplainProvidersResponse/RemoteAuthorizationItem/RemoteAuthorizationsPage）进入 schema.json 与 TS 镜像。

## Freeze 5：V0.1.6-alpha

冻结 Provider 接口、EvidenceContext、Explanation schema、引用校验和远程授权记录。模型输出永远不能改变确定性图。

### Freeze 5 登记（Batch-06 / LLM-001..003）

- EvidenceContext：证据只按 ID 从**当前分析**取（参数化）；空证据 400；跨分析/幻觉引用拒绝；snippet 预算 2000 字符/条、总计 12000、条目 12，超限截断并显式记录 degraded；上下文含 64 位 sha256 输入哈希；prompt 无工具调用、仓库文本置于 UNTRUSTED 围栏内并声明不可信。
- 输出校验：claims.evidence_ids 必须全部存在于当前上下文；kind ∈ {restatement, inference, unknown}；命令式文本（```bash/shell 块、sudo、curl|sh、shell 链）拒绝；任一 claim 违规则整条解释落库 `status=failed`（含错误原因），绝不冒充 completed。
- Provider：`fake`（确定性、离线、默认可用）；`ollama`（本地家族——端点必须 http/https 且解析为**环回**地址，拒绝私网/公网冒充本地；拨号直连已解析 IP 防 DNS rebinding；不跟随重定向）；`remote`（远程家族——端点必须**公网**地址，环回/私网/链路本地/保留/组播全拒绝；需同一请求内 `remote_consent{endpoint, acknowledged:true}`，按分析一次性消费）。
- 授权审计：每次远程授权落 `remote_authorizations`（analysis、provider、endpoint 主机、发送证据 ID 范围、上下文哈希、授权与使用时间）；消费即打标，可经 authorizations 端点查询。
- 事实边界重申：解释写入（explanations 表）不触碰 findings/evidence/maps/stages/analyses 任何确定性产物；分析状态机不含 explain 阶段；模型不可用/失败不影响既有报告端点。

## Change Request

文件：`docs/architecture-change-requests/ACR-YYYYMMDD-NNN.md`。必须包含问题、当前契约、候选方案、推荐方案、受影响任务、迁移、测试和回滚方式。未获 Codex 复核前不得合并。
