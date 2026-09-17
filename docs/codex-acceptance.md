# Codex 独立验收系统

Codex 默认不相信 Coding AI 的自报完成。每个任务执行 Requirement Traceability：Task → PRD → Code → Test。

## 集中验收与返工成本（PRD 0.23）

1. 在施工前确定批次边界矩阵和验收入口，Task ID 用于追溯，只有完整 Batch 交付后才进行人工交接。
2. 首次验收集中核对本批代码、产物和全部适用矩阵，独立运行必要检查，统一交付根因及同类场景，不逐个发现逐个结束。
3. 修复轮次读取本次变更与既有阻塞，重审受影响模块和对应反例；无新修改、依赖变化或安全迹象时，不重新人工扫描已经通过的无关模块。自动全套回归可保留，不要求每次工具调用都重跑。
4. 影响核心正确性、证据真实性、未检查范围、安全或冻结契约的缺陷阻塞；新能力、风格建议和不影响当批验收的改进进入后续计划。新增阻塞须指出既有要求、具体反例、影响，不用新需求无限扩大门槛。
5. 已修复项单独关闭。对未修复项要求解决同类根因，而不是只满足一个失败输入；独立验收仍可添加有依据的反例，提前公开的矩阵不是放弃正确性检查。
6. 保留简洁结果及本地原始日志；不重复粘贴代码/施工全过程。无 Git 提交基线时必须声明差异证明限制，不能把空 git diff 当作无改动证据。

## 验收等级

- A0 Static：文件、类型、依赖、冻结契约和敏感信息检查。
- A1 Unit：规则、解析、schema、错误和边界单元测试。
- A2 Integration：Git、解析、规则、Evidence、SQLite 集成。
- A3 API：状态码、schema、幂等、取消、分页和错误响应。
- A4 Browser：Playwright 验证当前视觉基线下的操作闭环。
- A5 E2E：真实 TypeScript fixture 完成 Git→报告全链路。
- A6 Security：路径穿越、symlink、命令执行、代码外发、prompt injection、Docker mount。
- A7 PRD：逐条核对需求、实现状态、验证状态和未完成限制。

## 验收命令

共享执行约束见根 [AGENTS.md](../AGENTS.md)；以下是当前仓库可用入口，不代表全部验收等级已实现。

```powershell
docker compose --profile fixtures run --rm --build fixture-setup
docker compose up --build -d web daemon
docker compose exec -T web pnpm exec tsc --noEmit
docker compose exec -T daemon pytest -q -o "addopts=-p no:cacheprovider"
docker compose exec -T daemon pytest tests/test_analysis_slice.py -q
```

按变更风险选择检查；最后一条是定向集成入口，已通过全套且没有新变更时不必重复。涉及 Docker 生产构建时执行 `docker compose --profile preview build preview`。

当前 Web 没有 `pnpm test` 脚本或已配置的 Playwright 测试套件，因此删除旧版中这两个不可用入口。A4 需要用当前可用浏览器工具实际操作并记录结果；A5 需待真实 API、存储与 Web 联动具备后执行，不能用 fixture 引擎测试冒充 Web 全链路。未来新增自动化入口应随实现更新本节。

使用独立 Compose 项目/端口验证时保留实际命令与数据来源。未运行、缺依赖、环境错误及适用等级尚无入口分别记录；不得改写为通过。文档/规则维护只做相关静态核对，不触发与行为无关的整套审计。

## 通过条件

任务的所有 Acceptance Criteria 通过；真实 fixture 被使用；Evidence 能回到文件、行号、提交和规则；Coverage 未检查范围透明；模型引用经过校验；旧功能无回归；Docker 边界通过。任何一项失败都标记 FIX_REQUIRED。

## 独立审计清单

代码、测试、运行日志、API schema、SQLite 迁移、错误恢复、路径 containment、symlink、仓库执行、远程授权、模型 prompt injection、证据行号、报告/UI 一致性和 PRD traceability。

## 验收输出

```text
Task ID：
PRD 条目：
检查等级：
执行命令：
代码证据：
测试结果：
安全结果：
回归结果：
未覆盖项：
结论：PASSED / FIX_REQUIRED / BLOCKED
```
