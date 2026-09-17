# Contributing / 参与贡献

[English README](README.md) · [简体中文 README](README.zh-CN.md)

MorphoJudge is early in development. Useful contributions start with a
reproducible question: a missed relationship, a misleading finding, a wrong
source location, or an unclear coverage boundary.

1. Read the [PRD](docs/PRD.md) and [contribution guide](docs/contribution.md).
2. Open an issue for changes to architecture or product behavior. Small
   documentation corrections can go directly into a pull request.
3. Keep the current UI and frozen contracts intact unless a change is agreed.
4. Include positive, negative and uncertain cases. Never turn unknowns into
   claims of safety, or model explanations into deterministic facts.
5. Prepare the fixture and run tests using the Docker commands in the README.
   Never execute the analyzed fixture's source, hooks or package scripts.

In your PR, explain the problem, what changed, how you verified it, and what
remains unsupported. If using AI assistance, review the result and test it:
generated code and an agent's completion message are not acceptance evidence.

For translations, keep the capability/status tables and runnable commands
consistent across both READMEs. Do not promote planned features to supported ones.

Only contribute material you have the right to share. No real credentials,
private repository content, AI chat transcripts or local analysis reports.
Sensitive vulnerabilities should not be disclosed in a public issue. A private
security reporting channel will be documented when it is established.

中文：请先阅读[详细贡献指南](docs/contribution.md)。新增能力先讨论问题与边界，
再实现和验证；文档纠错可以直接提交 PR。欢迎脱敏的误报/漏报复现、解析与规则改进、
测试、翻译和交互反馈。请勿提交私有代码、真实凭据、聊天记录或本地分析报告。
