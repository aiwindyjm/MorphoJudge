# 分析引擎规则

本文件补充根 [AGENTS.md](../AGENTS.md)，适用于 `engine/`。先读与改动相关的契约冻结项和测试，勿加载无关批次。

## 实现约束

- 使用现有 `morphojudge/git`、`selection`、`parser`、`relations`、`rules`、`evidence`、`analyzer` 边界。解析与规则不反向调用 Web/API；模型只消费已允许证据。
- Git 读操作走现有参数数组 runner 和命令白名单；不拼 shell，不因 ref/path 错误回退到任意命令或工作树。仓库配置、hooks、过滤器与外部 diff 不得造成输入代码执行。
- Selection 是范围决策入口，预览与实际分析复用决策；排除、失败和预算消耗可追溯。拒绝路径可以作为错误信息保留，绝不能因此成为可读文件路径。
- 快照绑定提交；ID、排序和序列化保持可复现。证据明确 old/new、快照、文件、规则、行号与片段；UTF-8 字节偏移不能直接切 Python 字符串。
- 类型引用、调用点分别保留 AST 位置，不能把方法首行当作全部引用位置。重复、嵌套、多行、Unicode 与别名需按支持范围测试。
- `resolved` 只用于已支持且有证据的解析；动态调用、反射、不支持语法和缺类型保留 candidate/unresolved。图遍历终止、预算截断及未解析路径必须可观察。
- Pydantic 契约以 `morphojudge/contracts/` 为源；同步生成 `packages/contracts/schema.json` 与 `app/lib/contracts.ts`，按冻结文档登记版本和兼容性。不要编辑生成 JSON 来掩盖源模型问题。
- Schema 不仅需要“生成一致”：引用必须可解析且能实际验证 payload。源码兼容、Schema 兼容和新产物完整性分别验证。不要新增未登记的副本契约。

## 验证

容器工作目录为 `/engine`。按修改范围执行 `docker compose exec -T daemon pytest tests/<相关文件>.py`；交付回归与日志遵循根规则。测试至少覆盖有效输入、合法反例、未知/失败及适用资源边界。使用真实 fixture 的集成验证见 `tests/test_analysis_slice.py`。

只测试本项目分析器；不导入、运行、编译或安装被分析 fixture。Fake Provider 成功不证明真实 Provider 可用。新服务/API/数据库必须属于已授权 Batch，不因目录规划而自行实现。
