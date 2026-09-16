# MorphoJudge AI 快速入口

本项目的完整规则位于根目录 [AGENTS.md](AGENTS.md)。Gemini/GLM 及其他 AI 工具必须先读取该文件；本文件不新增与其冲突的要求。

每次会话读取当天 `PRIVATE/conversations/YYYY-MM-DD.md`（若存在），并在会话结束时追加目标、决定、行动、文件、验证、未完成事项和下一步。不得记录隐藏推理、密钥、完整私有代码或敏感对话原文。
