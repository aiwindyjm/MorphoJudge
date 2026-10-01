# MorphoJudge v0.2 PRD 增量草案

版本：`0.25-draft`
状态：待用户评审
基线：[PRD 0.24](PRD.md)；v0.1.0 已发布（tag `v0.1.0`）
日期：2026-09-28

## 变更记录

| 版本 | 日期 | 变化 |
| --- | --- | --- |
| 0.25-draft | 2026-09-28 | 新增 FR-014～FR-017：Python 分析、Ollama 配置优化、依赖增强、报告筛选；扩展 §12.3 多语言边界 |

## 新增功能需求（v0.2）

### 优先级 1（纳入 v0.2）

| ID | 功能 | 必须满足 |
| --- | --- | --- |
| FR-014 | Python 语言分析 | Tree-sitter Python 语法解析；PY-* 规则族（网络 requests/urllib/httpx、Shell subprocess/os.system、文件 open/read/write、权限装饰器）；SelectionRules 扩展 .py 后缀；与 TS/JS 共用同一管线、证据锚定与覆盖语义 |
| FR-015 | Ollama 配置优化 | MORPHOJUDGE_OLLAMA_URL 一键配置；Provider 状态 UI 显示已配置模型列表（GET /api/tags 自动发现）；README 提供标准 Docker Compose 配置 |
| FR-016 | 依赖分析增强 | 支持 package-lock.json（npm）、yarn.lock（yarn）、requirements.txt / Pipfile.lock（pip）格式；新增/删除/版本变化检测；来源分类沿用既有 DEP-* 规则族 |

### 优先级 2（视工作量纳入或推迟至 v0.3）

| ID | 功能 | 必须满足 |
| --- | --- | --- |
| FR-017 | Web UI 报告筛选 | 按类别（网络/Shell/文件/权限/依赖/一致性）、影响（高/中/低）、复核状态（待复核/需调查/已确认/误报）筛选发现列表；筛选不改变事实总量 |

### 暂不进入（v0.2 不实现）

| 方向 | 原因 |
| --- | --- |
| 图数据库 | 轻量结构索引已够用；等关系查询成为瓶颈再评估 |
| 自动修复 | 不在 v0.x 范围 |
| SaaS / 多租户 | 与 Local First 原则冲突 |
| CI/IDE 集成 | 需独立产品决策 |
| 生产部署编排 | Docker Compose 已够用 |
| Go/Rust/Java 语言 | 等 Python 验证多语言管线后再扩展 |

## §12.3 能力边界扩展

原文"首个语言"指 TypeScript/JavaScript。v0.2 扩展为：

- **支持语言**：TypeScript、JavaScript、Python
- **共用管线**：Selection → Parse → IR → Rules → Evidence → Finding
- **语言适配层**：每种语言一个 Tree-sitter 语法模块 + 一个规则族文件
- **Selection 扩展**：`.py` 后缀映射为 `python`；SUPPORTED_LANGUAGES 追加
- **规则命名**：Python 规则以 `PY-` 前缀（如 PY-NETWORK、PY-SHELL），与 BEH-* / DEP-* 平行
- **不改变**：证据锚定语义、覆盖限制语义、影响路径算法、契约结构

## 验收标准

| 项 | 标准 |
| --- | --- |
| Python fixture | 含 imports、网络调用（requests/urllib）、subprocess、文件操作、权限装饰器的脱敏 Python 仓库 |
| PY 规则族 | PY-NETWORK/PY-SHELL/PY-SHELL/PY-FILE/PY-PERM 至少各 1 个正例 + 1 个反例 |
| 混合仓库 | 同时含 .ts 和 .py 的仓库可正常分析，两种语言的发现共存 |
| Ollama | docker compose --profile ollama up 一键启动；UI 显示模型列表 |
| 依赖 | npm/yarn/pip 锁文件变更被检测；DEP-ADD/REMOVE/VERSION-CHANGE 规则沿用 |
| 回归 | 既有 352 pytest + 41 e2e 全部通过；新增 Python 测试 ≥ 30 项 |

## 用户评审要点

1. Python 是否纳入 v0.2 首批？（是/否/缩小范围）
2. 依赖增强支持哪些格式？（npm + pip 优先？yarn 是否必要？）
3. Ollama 是否需要 compose 集成？（或仅文档+配置？）
4. FR-017 报告筛选是否纳入 v0.2 或推迟？
5. Go/Rust 是否需要在 roadmap 中登记计划时间？
