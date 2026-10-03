# MorphoJudge 实施路线图

版本不是日期承诺；每个 Batch 完成后，ZCODE 必须停止，Codex 独立审计通过后才可进入下一批次。Batch 与 GitHub Release 的候选版本、门槛和授权边界见 [release-strategy.md](release-strategy.md)。

PRD 0.23：Batch 是完整能力交付单位，Task 是内部追溯项，不逐项人工交接。

2026-09-20 状态核对：Batch-01、Batch-02 已 PASSED；Batch-03 实现与测试已存在，其完整独立验收记录尚未闭合，不能继续写成”尚未实现”，也不据后续回归自动标为 PASSED。Batch-04 的 SQLite、Worker、API 交付范围经 R1/R2/R3 集中修复及独立增量复核，已 PASSED；该结论不代替 Batch-03 全能力验收或 v0.1 浏览器/模型/导出全链路验收。Batch-05（真实 Web API 接入）已完成施工：WEB-001/002/003 连同同源代理、浏览器回归入口已实现并于 2026-09-23 在隔离 Compose 项目完成终验（tsc、Playwright e2e 31/31、pytest 335 全部 exit 0）；状态为 **TESTED**，等待 Codex 独立验收，不因自测结果标记 PASSED。

当前发布状态：仓库已有多个开发提交，但不按提交次数自动创建 Release。当前工作区存在未提交的 Batch-05 Web 接入变更，且 R3 仍未提交，因此为 `NO_RELEASE`。提交或推送前必须填写 [release-strategy.md](release-strategy.md) 的 Release decision。

## Batch-01 / V0.1.0-alpha：契约、Docker、Git 快照与真实 fixture

任务：ARC-001、DATA-001、FIX-001、OPS-000、GIT-001、GIT-002、SEL-001。
依赖：无；Batch 内按 ARC→DATA→FIX→OPS→GIT→SEL 顺序施工。
产出：可运行的分析服务骨架、TypeScript Web fixture、不可变 SnapshotIdentity、Diff 和 CoverageSummary。
发布门槛：Docker 启动；合法/非法 ref、dirty、symlink、binary、rename、删除、超大文件有明确状态；重复输入生成相同快照 ID。

## Batch-02 / V0.1.1-alpha：TypeScript 解析与关系 IR

任务：PARSE-001、PARSE-002、PARSE-003、REL-001。
依赖：Batch-01 PASSED。
产出：页面、事件、方法、契约、调用、数据库和行为调用的带位置 IR。
发布门槛：fixture 所有必需实体可定位；别名、动态调用、解析错误、循环依赖进入正确状态；不执行 fixture。

## Batch-03 / V0.1.2-alpha：规则、证据与发现

验收状态：定向抽查通过，独立验收欠账闭合（2026-09-26，见 [PRIVATE/verification/2026-09-26-b06-codex/audit.md](../PRIVATE/verification/2026-09-26-b06-codex/audit.md) B03 抽查节）。8 项反例中 5 项直接通过，3 项探针 fixture 偏差经既有 352 项回归测试覆盖确认。因非完整独立验收流程，不标 PASSED。

任务：BEH-001、DEP-001、EVD-001、ANL-001、TEST-001、SEC-001。
依赖：Batch-02 PASSED。
产出：行为/依赖/一致性 Finding、EvidenceAnchor、RelationPath 和影响查询。
发布门槛：每条 Finding 有证据或明确 unresolved 原因；未检查不显示为安全；路径和命令边界测试通过。

施工与预先验收矩阵见 [implementation-tasks.md](implementation-tasks.md)「Batch-03 完整能力规格」。一次性交付 Docker 内 Python 分析入口与真实 fixture 集成测试。SEC-001 本批仅检查分析边界，API/模型相关安全检查在对应批次补齐，不因此提前开发未来模块。

## Batch-04 / V0.1.3-alpha：SQLite、Worker 与 API

验收状态：**PASSED（2026-09-20，限本批交付范围）**。代码基线为 `09dcb3f` 加 R3 的 manifest 校验与公开测试修复；R3 尚未提交。最后一轮独立运行相关集成测试 82 项及 null/缺省探针 8 项，全部通过；335 项全套通过是施工方本轮回执结果，Codex 本轮未重复全套。迁移数据保留、执行所有权、取消及错误回显的独立验收沿用上一轮已关闭结论。

任务：DB-001、ANL-003、API-001、API-002。
依赖：Batch-03 PASSED。
产出：事务持久化、阶段 checkpoint、轮询/取消/恢复和查询 API。
发布门槛：重启可恢复；旧快照不被覆盖；API schema、错误码和幂等行为稳定。

## Batch-05 / V0.1.4-alpha：真实 Web API 接入

验收状态：**TESTED（2026-09-23 终验，等待 Codex 独立验收）**。实现包含 WEB-001（运行时响应校验、类型化客户端、Next 服务端白名单同源代理、创建/轮询/取消/刷新恢复）、WEB-002（SoftwareMap 适配层、三态 resolution 无损、组件 props 化）、WEB-003（产物分层查询、覆盖/发现/证据/影响展示、服务端分页）。浏览器回归入口已建立（`playwright.config.ts`、`tests/e2e/`、`pnpm test:e2e`、Compose `e2e` profile）。真实限制：review/导出/模型解释 UI 属后续批次（入口禁用并标注）；发布决策保持 `NO_RELEASE`（候选版本 `0.1.0-beta.1`，见 release-strategy.md）。

任务：WEB-001、WEB-002、WEB-003。
依赖：Batch-04 PASSED。
产出：当前原型通过 API 使用真实 fixture 分析数据、覆盖、关系和证据。
发布门槛：不改变当前视觉与交互；fixture 不再作为真实分析默认数据源；浏览器完成加载、筛选、下钻、证据定位和错误重试。

## Batch-06 / V0.1.5-alpha：本地模型闭环

任务：LLM-001、LLM-002、LLM-003、WEB-004。
依赖：Batch-05 PASSED。
产出：受限 EvidenceContext、Fake/Ollama Provider、引用校验、按需解释和远程按次授权。
发布门槛：模型失败不阻塞基础报告；候选关系不改变事实图；授权范围和发送记录可审计。

## Batch-07 / V0.1.6-alpha：报告、复核与最终 E2E

任务：RPT-001、TEST-002、ACC-001。
依赖：Batch-06 PASSED。
产出：Markdown/JSON、人工复核和完整验收报告。
发布门槛：Codex A0-A7 通过，UI/API/导出事实一致，安全边界和覆盖限制完整记录。

## Batch-08 / v0.2-alpha：Python 语言分析

验收状态：**PASSED（2026-09-28，限本批交付范围）**。提交 `0bd12db`。
Tree-sitter Python 语法适配、符号/导入提取、行为边提取（网络/进程/文件模式与 TS 共用 BEH-* 规则）；19 项测试 + 混合仓库验证 + 371 pytest 全套回归零破坏。

任务：PY-001、PY-002、PY-003。

## Batch-09 / v0.2-alpha：Ollama 配置 + pip 依赖

验收状态：**PASSED（2026-09-28，限本批交付范围）**。提交 `e0e41da`。
Ollama compose profile + 模型自动发现 + README 配置指南；requirements.txt / Pipfile.lock 解析与 DEP-ADD/REMOVE/VERSION-CHANGE 检测；18 项测试 + 389 pytest 全套回归零破坏。

任务：OL-001、DEP-002。

## 暂不进入路线

完整多语言（Go/Rust 待 Python 验证后评估）、图数据库、自动修复、SaaS、CI/IDE 集成、生产部署、云端默认模型和复杂 Agent。报告筛选（FR-017）推迟至 v0.3。
