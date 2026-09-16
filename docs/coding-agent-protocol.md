# Coding AI 协作协议

版本：0.3
状态：批次执行协议

## 批次节奏

MorphoJudge 采用“ZCODE 批次施工 → ZCODE 停止并交付 → Codex 独立审计 → 决定下一批次”的闭环。ZCODE 一次只处理一个 `Batch`，Batch 内可以按依赖顺序完成多个 Task；ZCODE 不得跨 Batch 继续开发，也不得在交付后自行宣布产品或批次通过。

每个 Batch 必须有：

- 固定的 Batch ID 和 Task 列表；
- 已通过的前置 Batch；
- 允许修改文件范围；
- 明确的批次交付物；
- 批次级测试命令；
- 停止条件和已知限制。

Batch 状态：`PLANNED → READY → IMPLEMENTING → IMPLEMENTED → TESTED → CODEX_REVIEW → PASSED`。失败使用 `FAILED → FIX_REQUIRED → RETEST`。Codex 未标记 `PASSED` 前，Batch 保持 `CODEX_REVIEW`，ZCODE 不进入下一批次。

## 任务状态

`PLANNED → READY → IMPLEMENTING → IMPLEMENTED → TESTED → CODEX_REVIEW → PASSED`。失败使用 `FAILED → FIX_REQUIRED → RETEST`。

## 执行规则

1. 一次领取一个完整能力 Batch；Task ID 是内部追溯项，按依赖连续推进，不逐 Task 停止、请求确认或交给 Codex。
2. 开始前读取 PRD、策略、路线图、Batch 规格、任务依赖和契约冻结点。
3. 检查前置 Batch 已由 Codex 标记 `PASSED`；同批次依赖由 ZCODE 自己检查。可先完成跨模块集成再集中验收，不以文书状态阻断正常施工。
4. 修改范围以批次内各 Task 文件范围的并集及明确授权的集成文件为准，允许必要的内部函数/数据结构调整；不顺手重构或引入其他批次能力。
5. 不修改 PRD、冻结契约、导航、视觉基线或其他 Batch 的文件；需要变化时提交 ACR 并停止受影响 Task。
6. 不执行目标仓库代码、hooks、包脚本或任意命令；只运行本项目规定的验证命令。
7. 先列出整批边界矩阵，再实现并运行与修改相应的测试；失败时在本批内自行定位和修复。最终统一回归，只有新增修改或未解决风险才重跑相关检查，不每改一个函数就重跑全套。
8. 完成 Batch 后运行批次测试，生成交付回执，然后立即停止，不领取下一 Batch。
9. 记录修改文件、测试结果、限制、未完成事项和实际状态；不能用未运行的测试声称通过。
10. 只有 Codex 标记 Batch 为 `PASSED` 后，才允许领取下一 Batch。

## Batch 交付回执

```text
Batch ID：
包含 Task：
前置 Batch：
本批目标：
实际完成 Task：
实际修改文件：
新增接口/数据结构：
批次测试命令与原始结果：
安全检查结果：
未完成 Task：
已知限制：
是否改变冻结契约：是/否；如是，ACR：
当前 Batch 状态：TESTED
已停止等待 Codex 审计：是
```

## 交付模板

```text
执行 Task：
读取：PRD、implementation-strategy.md、任务依赖、当前代码
目标：
允许修改：
禁止修改：
实现步骤：
1.
2.
3.
必须创建/修改：
测试命令与结果：
实际修改文件：
已知限制：
Acceptance Criteria：PASS/FAIL
当前状态：TESTED
是否建议下一任务：是/否
```

需求冲突必须停止受影响写入，报告冲突位置、实际状态、PRD 要求、技术影响和方案；无冲突部分继续。

## 目标模式交付约定（PRD 0.23）

- 开始时只需一段范围说明和 3–7 步内部计划；不要只交计划、骨架、Fake 或单个 Task 后结束。
- 工具调用结束、上下文压缩、普通测试失败不等于需要用户接力；通过私有账本记录进度后在当前目标内继续。无法继续时准确说明环境/权限/契约阻塞，不冒充已完成。
- 每个能力覆盖：正例、合法反例、重复/嵌套/多行/Unicode、候选与未知、失败隔离、预算和安全边界。矩阵在实现前确定，过程中发现同类情况一起补齐。
- 已有失败反例是回归下限，不是支持范围上限。修复应解决表示模型或算法根因，不能只匹配样例内容。
- 最终回执增加：边界矩阵→测试的对应表、可运行入口、原始输出位置、工作区起止差异清单（无提交基线时用路径+内容哈希，本地保存，不能靠空 git diff 证明无改动）。
- 实现方状态至多 `TESTED`；只有独立 Codex 能标 `PASSED`。后续验收与返工方法见 codex-acceptance.md 的集中验收条款。
