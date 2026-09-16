# MorphoJudge 实施策略

版本：0.2
状态：实施基线草案
依据：[PRD](PRD.md) 0.23、architecture.md、tech-stack.md、roadmap.md、issues.md。

## Executive Summary

当前仓库保留已确认视觉交互的 Next.js 原型。2026-09-16 Batch-01/02 已通过独立验收：真实 Git/Selection、TS/JS 解析与关系 IR、daemon 健康接口已建立；规则闭环、SQLite、真实业务 API 和 Ollama 尚待对应批次实现，Web 尚未完成真实分析数据接入。v0.1 的唯一技术目标是用一个 TypeScript Web fixture 完成 Git → Selection → Parser → SoftwareMap → Rules → Evidence → Finding → SQLite → API → Web → Review → Export 的可重复纵向闭环。

施工以完整能力 Batch 为单位，Task 仅作内部追溯；批次内持续实现和自测，完成后一次性交付。下一轮以 implementation-tasks.md「Batch-03 完整能力规格」为入口，提前检查边界矩阵，按 coding-agent-protocol.md 0.3 执行；不逐函数/逐 Task 交给 Codex 审计。

事实层、规则层、候选层、未知层、模型解释层、人工复核层必须独立存储和渲染。模型只能引用当前快照的证据，不能修改事实图。未检查不等于无风险。

## 分层与数据流

1. Repository/Snapshot：受限读取仓库，解析 base/target，生成不可变 SnapshotIdentity。
2. Selection/Coverage：按语言、文件类型、大小、路径和边界规则生成 SelectionDecision 与 CoverageSummary。
3. Parser：TypeScript/JavaScript Tree-sitter 加类型/符号适配，输出带位置的中间实体。
4. IR：Page、Function、Method、Contract、Data/ExternalService 及带来源的关系边。
5. Rules：行为、依赖、一致性和变更影响规则；输出 Finding 草稿与 EvidenceAnchor。
6. Evidence：统一文件、快照、old/new、行号、片段、rule_id 和 resolver 状态。
7. Finding：关联证据、关系路径、覆盖限制和人工 Review。
8. Explanation：本地 Provider 按需消费证据上下文；输出只能引用 Evidence ID。
9. Persistence：SQLite 事务保存分析、覆盖、IR、证据、发现、解释、复核和任务状态。
10. API：FastAPI 轮询式异步任务，统一错误对象和 schema 版本。
11. Web：现有原型只负责请求、状态、浏览、解释、复核和导出，不运行分析逻辑。

## 安全边界

分析服务不执行目标仓库代码、hooks、包脚本、插件或任意 shell；Git 参数使用参数数组和 `--end-of-options`；仓库路径必须真实路径归一化、符号链接检查并限制在登记根目录；源码文本和模型输出均是不可信数据，不能产生工具调用。远程模型默认关闭，单次分析授权并记录发送范围。

## 最小纵向 fixture

fixture 必须包含路由、页面事件、方法调用、API/TypeScript 契约、数据库读写，以及 fetch、child_process、fs 或权限行为线索；同时提供基线和目标提交、正常样例、候选/动态样例、解析失败样例和重命名/删除样例。

## 关键控制流

创建分析先冻结输入身份，再生成覆盖清单；覆盖清单完成后才能解析；解析错误进入 Coverage，不中止可继续的文件；规则只消费 IR，不读取原始任意路径；Evidence Resolver 统一定位；Finding 只有证据后才能持久化；模型解释只在 Finding/Node 已有证据时按需执行；复核和导出读取同一 SQLite 事务视图。

## 技术选择

保持 Next.js/TypeScript、FastAPI/Pydantic、Tree-sitter、SQLite、Docker Compose、Ollama。首版不用图数据库、消息队列、SaaS、自动修复或完整多语言解析。

## 失败与恢复

每个阶段记录 queued/running/completed/failed/skipped；分析可进入 completed_with_limits。取消只停止 MorphoJudge worker，不杀任意用户进程；重试使用同一 snapshot_id 和规则版本，禁止覆盖旧结果；恢复从最后一个事务完成的阶段继续。

## 任务依赖图

```text
ARC-001 → DATA-001 → FIX-001 → GIT-001 → GIT-002 → SEL-001
                                      ↓                 ↓
                              PARSE-001 → PARSE-002 → PARSE-003
                                                       ↓
                                               REL-001 → BEH-001/DEP-001
                                                           ↓
                                               EVD-001 → ANL-001 → DB-001
                                                                        ↓
                                             API-001 → ANL-003 → API-002
                                                                        ↓
                                  WEB-001 → WEB-002 → WEB-003 → WEB-004
                                                                        ↓
                                                  RPT-001 → TEST-002 → ACC-001
```

可并行：ARC-001 完成后 DATA-001 与安全文档；FIX-001 与测试骨架；PARSE-001 与 Docker 环境；BEH-001 与 DEP-001；LLM-001 与 Web API schema。不可并行：未冻结 DATA-001 不得写解析器；未完成 EVD-001 不得写 Finding 或模型 Provider；未完成 API-002 不得替换 Web fixture；未通过真实 fixture 不得进入发布验收。

