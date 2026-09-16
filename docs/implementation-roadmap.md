# MorphoJudge 实施路线图

版本不是日期承诺；每个 Batch 完成后，ZCODE 必须停止，Codex 独立审计通过后才可进入下一批次。

PRD 0.23：Batch 是完整能力交付单位，Task 是内部追溯项，不逐项人工交接。2026-09-16 的独立验收结论：Batch-01、Batch-02 已 PASSED；Batch-03 尚未实现。Batch-02 最近验证为 176 项 pytest、tsc 及来源定位反例通过，不代表后续批次已完成。

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

任务：BEH-001、DEP-001、EVD-001、ANL-001、TEST-001、SEC-001。
依赖：Batch-02 PASSED。
产出：行为/依赖/一致性 Finding、EvidenceAnchor、RelationPath 和影响查询。
发布门槛：每条 Finding 有证据或明确 unresolved 原因；未检查不显示为安全；路径和命令边界测试通过。

施工与预先验收矩阵见 [implementation-tasks.md](implementation-tasks.md)「Batch-03 完整能力规格」。一次性交付 Docker 内 Python 分析入口与真实 fixture 集成测试。SEC-001 本批仅检查分析边界，API/模型相关安全检查在对应批次补齐，不因此提前开发未来模块。

## Batch-04 / V0.1.3-alpha：SQLite、Worker 与 API

任务：DB-001、ANL-003、API-001、API-002。
依赖：Batch-03 PASSED。
产出：事务持久化、阶段 checkpoint、轮询/取消/恢复和查询 API。
发布门槛：重启可恢复；旧快照不被覆盖；API schema、错误码和幂等行为稳定。

## Batch-05 / V0.1.4-alpha：真实 Web API 接入

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

## 暂不进入路线

完整多语言、图数据库、自动修复、SaaS、CI/IDE 集成、生产部署、云端默认模型和复杂 Agent。
