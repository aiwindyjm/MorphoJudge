# MorphoJudge Release Strategy

本文件定义 Git 提交、push、GitHub Release 与产品版本的关系。提交不自动产生 Release。

## 动作边界

`commit` 保存开发变更，`push` 同步远端分支，`tag` 固定发布提交，GitHub Release 发布标签、说明和验证边界。四个动作必须分别记录；禁止移动已有标签、强推或用未验证工作区发布。

创建公开 Release、推送标签和发布附件需要本次会话的明确授权。满足条件只能标记候选，不能自行发布。

## 版本轨道

| 版本 | 含义 | 门槛 |
| --- | --- | --- |
| `0.x.y-alpha.N` | 通过的基础开发批次，仍可能改变契约 | 对应 Batch 经 Codex `PASSED` |
| `0.x.y-beta.N` | 主要纵向链路稳定 | A0-A5、真实 fixture 和公开复现流程 |
| `0.1.0` | PRD v0.1 本地产品闭环 | A0-A7，含真实 Web、模型降级、报告和复核 |
| `1.x.y` | 稳定公共契约 | 当前不规划 |

## 默认批次映射

| Batch | 候选版本 | 说明 |
| --- | --- | --- |
| 01 | `0.1.0-alpha.1` | 契约、Git、fixture、daemon 骨架 |
| 02 | `0.1.0-alpha.2` | TypeScript 解析与关系 IR |
| 03 | `0.1.0-alpha.3` | 规则、证据与发现 |
| 04 | `0.1.0-alpha.4` | SQLite、Worker、API |
| 05 | `0.1.0-beta.1` | 真实 Web/API 浏览器闭环 |
| 06 | `0.1.0-beta.2` | 本地模型解释闭环 |
| 07 | `0.1.0` | 报告、复核、导出和 A0-A7 |

候选版本不是自动标签。多个兼容修复提交可递增 `alpha.N`；不得跳过未发布的事实。

## 提交前判断

每次提交或 push 前必须在回执填写 Release Decision：

| 状态 | 判断 |
| --- | --- |
| `NO_RELEASE` | 仅文档、测试、内部重构、未完成 Batch，或仍有阻塞 |
| `RELEASE_CANDIDATE` | Batch 已 Codex `PASSED`，产物可复现，等待发布授权 |
| `RELEASE_AUTHORIZED` | 本次明确授权且发布检查全部通过，可创建 tag/Release |

提交次数、测试数量、代码已存在或 push 成功，都不能单独触发 Release。契约、迁移、Docker 或用户可见功能变化必须同时提供迁移说明、版本说明和完整验证矩阵。安全、隐私、数据丢失、不可恢复迁移或公开内容污染时禁止发布。

## 发布检查

候选或正式 Release 必须提供：标签目标 commit 与远端同步证明；PRD/Batch/Codex 结论；Docker、pytest、tsc、Playwright、fixture 和安全命令的真实结果；Schema/API/迁移变更；README 能力边界；公开内容扫描；回滚方式。

Release Notes 固定包含 `Added`、`Changed`、`Fixed`、`Validation`、`Known limitations`、`Not included`。不得把未知写成安全、示例写成真实或 TESTED 写成正式稳定。

## 回执模板

```text
Release decision: NO_RELEASE / RELEASE_CANDIDATE / RELEASE_AUTHORIZED
Candidate version:
Reason:
Required gates:
Passed gates:
Missing gates:
Tag/release action: not requested / authorized / completed
```
