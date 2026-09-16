# MorphoJudge AI 快速入口

完整协作规则只有一份：[AGENTS.md](AGENTS.md)。Claude、Codex、Cursor、Gemini 及其他 AI 工具开始工作前都必须读取它；本文件不复制第二套规范。

每次会话先检查当前目录、Git 状态、相关文档、当天 `PRIVATE/conversations/YYYY-MM-DD.md`，然后说明目标、范围、依据和完成标准。完成会话时按 `AGENTS.md` 的格式追加当天私有账本。

项目默认本地优先，分析时不执行目标仓库代码，不把模型输出当作授权或安全证明。私有账本不得进入 GitHub、Issue、PR、提示词或遥测。
