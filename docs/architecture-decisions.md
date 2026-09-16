# 架构决策记录

| ID | 决策 | 理由 | 不采用 | 状态 |
|---|---|---|---|---|
| ADR-001 | Web + 本地 daemon | 浏览器不应直读仓库，分析需本地边界 | 云端 SaaS 直传源码 | Accepted |
| ADR-002 | TypeScript/JavaScript 首个生态 | 与当前 Web fixture 和目标用户最贴合 | v0.1 多语言 | Accepted |
| ADR-003 | 代码事实 + 人工确认定义功能 | 避免模型幻觉成为业务事实 | 仅靠方法名或模型自动确认 | Accepted |
| ADR-004 | 确定性事实优先，模型按需解释 | 模型失败不能阻塞证据链 | 模型作为扫描前置 | Accepted |
| ADR-005 | SoftwareMap 作为前端过渡契约 | 保持已确认原型不变 | 先重构 UI 再接数据 | Accepted |
| ADR-006 | SQLite + 事务 checkpoint | 本地、可恢复、部署简单 | v0.1 PostgreSQL/图数据库 | Accepted |
| ADR-007 | 远程模型每次分析授权 | 私有代码默认不外发 | 自动 fallback 远程模型 | Accepted |
| ADR-008 | OpenCodeReview 不作为借鉴基线 | 产品理念和流程不一致 | 移植其界面/Provider/流程 | Accepted |

任何改变 Accepted 决策的修改必须提交 Architecture Change Request，写明影响和迁移。

