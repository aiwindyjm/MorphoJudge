# MorphoJudge 实施任务清单

任务 ID 用于需求、代码和测试追溯，不要求逐项领取或人工验收。状态初始为 PLANNED；以完整能力 Batch 一次性交付，内部按依赖连续实现和自测。Batch 规格和停止门禁见 [coding-agent-protocol.md](coding-agent-protocol.md) 与 PRD §10.1。

## Batch 映射

| Batch | 任务 |
|---|---|
| Batch-01 | ARC-001、DATA-001、FIX-001、OPS-000、GIT-001、GIT-002、SEL-001 |
| Batch-02 | PARSE-001、PARSE-002、PARSE-003、REL-001 |
| Batch-03 | BEH-001、DEP-001、EVD-001、ANL-001、TEST-001、SEC-001 |
| Batch-04 | DB-001、ANL-003、API-001、API-002 |
| Batch-05 | WEB-001、WEB-002、WEB-003 |
| Batch-06 | LLM-001、LLM-002、LLM-003、WEB-004 |
| Batch-07 | RPT-001、TEST-002、ACC-001 |

Batch 内任务仍遵守 Depends On；未列出的任务不得隐式加入批次。

## Batch-03 完整能力规格（PRD 0.23）

状态：计划规格，可施工；不代表已实现。前置：Batch-02 已由 Codex 标记 PASSED（2026-09-16），当时验证 176 项 pytest、tsc 和两组来源定位反例通过；测试数量不是后续质量门槛。

**交付目标**：对固定 Git base/target 和 TypeScript fixture，单次调用完成
`Selection → 两侧解析/关系 → 行为/依赖/一致性规则 → Evidence → Finding → 影响路径 → 覆盖与限制`。
BEH-001、DEP-001、EVD-001、ANL-001、TEST-001、SEC-001 是这一目标的内部检查项，不分六次交接。
本批交付可在 Docker 内调用的 Python 服务及真实 fixture 集成测试，不提前开发 SQLite、异步任务、业务 API、模型、UI 或正式报告导出。

### 集成接口及范围

- 入口落在 `engine/morphojudge/analyzer/service.py`，提供可直接调用的 `analyze_snapshot(repo, snapshot, manifest, rules)`；仓库须先通过现有根目录校验，内容只从 snapshot 绑定的提交对象读取。命名参数的最终类型在实现中明确。
- 结果为内部 `AnalysisResult`：含输入身份、两侧 SoftwareMap、`Finding[]`、`EvidenceAnchor[]`、影响路径、选择/阶段覆盖和限制/错误。不把该内部聚合声明为已经冻结的 HTTP/持久化契约。
- 沿用现有 Finding/EvidenceAnchor。证据定位失败可用内部 `EvidenceResolution` 表示（状态、原因、可空 anchor）；成功后才创建准确 anchor。Finding 有证据或明确 unresolved 原因，不能构造假片段。PRD 的 resolver 语义不能因现有 anchor 暂无字段而丢失。
- 变更分类（existing/new/deleted）、路径 resolution、规则运行状态等先放内部聚合/元数据，并以 ID 关联现有对象，避免为了本批改写冻结结构。此内部表示属于批次规格；未来 API/DB 契约由相应批次冻结。
- 内部路径至少含起点、方向、节点/边 ID、证据 ID、resolution、truncated、限制原因；混合候选路径不得标为确定影响。返回路径只指向该结果内对应快照侧的图。
- 允许修改：本批任务的 rules/、evidence/、analyzer/、engine/tests/，以及必要的 pipeline.py、parser/、relations/、git/、selection/ 内部集成；可新增脱敏测试数据和 fixture golden。默认保留现有 fixture base/target，不 amend；缺失测试场景用测试临时 Git 仓库补齐，禁止执行被分析代码。
- 可更新本批规则说明/矩阵、任务测试映射及测试依赖（只在 Docker 中锁定安装）。不改 PRD、UI、冻结契约；如确需公开契约变更按 ACR 处理，不把它当普通实现细节。

### 提前明确的跨层边界

1. Selection 是源码和依赖文件的共同入口。现状默认只选 TS/JS；DEP-001 需要新增独立的依赖分析选择用途，对仓库根 `package.json`、`pnpm-lock.yaml` 做明确白名单和预算决策，并复用既有路径/私有/凭据/symlink 安全检查。不把所有 JSON/YAML 一并交给源码解析器，不绕过 Selection 偷读文件。此项是内部选择用途扩展，不改变原源码选择结果。
2. 首批依赖实现：package.json 的 dependencies/devDependencies/optionalDependencies/peerDependencies，及 pnpm lockfileVersion 9 的直接依赖、解析版本/来源。其他锁格式、损坏/冲突内容记为未支持或受限；不安装包、不查 registry/CVE。无 lockfile 不伪装成已检查。
3. 一致性仅检查可验证声明：JSDoc 参数/返回类型、显式必填类型/返回对象、明确无副作用声明。只在可以绑定到方法和可确定的返回/调用结构时形成规则提示；缺注释、自然语言含糊或动态结构保留未知。支持格式和 rule_id 在实现前写入本任务相应规则说明；不猜测任意自然语言与实现的等价性。
4. 权限线索只表示明确检查/保护调用及其变更，不通过 isAdmin 等短名称判断安全；守卫删除可给变更提示，不能直接结论“鉴权绕过”。Shell 注册表分发沿用候选状态，不要求执行或伪造直接调用。
5. 源码片段保持 Git blob 的原始文本语义；HTML 作为数据，不执行、不先转义后冒充源码。展示层转义属于以后 Web 批次，本批测试文本安全和不执行边界。
6. 行位置从 AST/token 或结构化解析位置得到；diff 删除用 base/old，新增用 target/new，重命名分别验证两侧路径。跨行、Unicode、重复调用/引用按实际位置追溯；无法精确映射保持 unresolved，不用方法首行或近邻行替代。
7. base/target 稳定身份不能只靠含行号的 node_id 直接匹配，否则插入空行就误判新增行为。精确对齐使用 diff/rename 与符号来源；有歧义时保留限制，不把“无法配对”伪装成确定新增/删除。
8. 按文件与分析用途记录覆盖：选择、解析、规则、依赖各自的完成/失败/限制都可查。保留原始 SelectionSummary；最终结果提供阶段覆盖，不能把“被选中”当“分析完成”。无可分析输入时明确失败，有局部问题时输出部分结果和限制。

### 批次验收矩阵（实现前读，交付时补测试映射）

| 编号 | 正常/反例/边界 | 必须能验证的结果 |
|---|---|---|
| B03-01 | 原真实 fixture；相同快照重复运行；空 diff；仅插入空行 | 同输入同结果/ID（除独立耗时字段）；existing/new/deleted 不因无语义改动乱变；不将空 diff 写成安全 |
| B03-02 | 网络/Shell/文件/权限线索；正常合法调用；同名局部函数；注释/字符串含危险词 | 规则描述静态线索，有来源；合法调用不自动等于漏洞；不能凭文本/短名称制造确定行为 |
| B03-03 | 动态地址/注册表、嵌套/多行调用、别名和无法绑定的第三方方法 | candidate/unresolved 不升级；每条已有引用位置准确；未知目标和未支持范围透明 |
| B03-04 | 依赖增删/版本/registry 与 Git URL 来源变化；pnpm v9；缺失/损坏/冲突锁文件 | 具体 JSON/YAML 位置可追溯，来源变化不等于恶意；所有未支持情况记录原因；零依赖安装/查询 |
| B03-05 | JSDoc 参数/返回/必填字段/副作用正反例；无声明/多分支/含糊文本 | 可确定差异为规则提示；正常不误报；不确定为未知；无副作用声明不构成安全证明 |
| B03-06 | Evidence 新增/删除/重命名、重复片段、跨行、Unicode/CRLF；越界/错误提交/截断 | 精确回到对应 commit/path/行/token；片段与 blob 一致；缺失/截断不能冒充完整准确证据 |
| B03-07 | 一个方法多个上游页面、循环、候选边、深度/节点预算、孤立节点 | 路径终止、来源可查、候选传播、截断有原因；未查到路径不承诺全局无影响 |
| B03-08 | 单文件解析失败、规则失败、全排除、超大文件/总预算；锁文件分析单独失败 | 有正常结果仍保留；每种失败均有阶段/对象/原因；覆盖计数与明细一致 |
| B03-09 | PRIVATE/凭据、路径穿越、symlink、子模块/LFS/binary；恶意注释 | 在读内容前限制选择；不执行目标源码/hooks/脚本，不联网、不泄露排除内容；测试通过 marker/禁止网络探针验证 |
| B03-10 | Finding→Evidence→snapshot，路径→边→节点；重复发现、多次引用、悬空引用 | ID 稳定且无悬空/错侧引用；去重不丢不同 rule/位置；未知项有原因；已有 Schema/类型回归继续通过 |

这些场景是最低公开验收矩阵，不是仅需匹配十个样例。ZCODE 自己推导同类输入，在本批内补测试并修复根因；无需把每个边界单独交给用户。

### Batch-03 规则 ID 与声明格式（实现前登记）

行为（`rules/behavior.py`；线索均为静态事实，kind=fact/impact=low，例外注明）：

| rule_id | 触发（确定性条件） | 输出 |
|---|---|---|
| BEH-NETWORK | `sends → svc:*` 边（fetch 调用点；动态目标保持 candidate/unresolved） | fact，existing/new/deleted |
| BEH-SHELL | `sends → data:process:*` 边（child_process 注册表分发，candidate） | fact |
| BEH-FILE | `reads/writes → data:file:*` 边（fs 操作） | fact |
| BEH-PERM-CHECK | resolved `calls` 边指向 manifest `permission_modules` 声明模块内的符号 | fact（存在性，非安全结论） |
| BEH-PERM-GUARD-REMOVED | 上述权限检查调用在 target 消失（方法删除或调用移除） | rule_hint/medium，措辞为变更提示，不作鉴权绕过结论 |

依赖（`rules/dependency.py`；零安装、零 registry/CVE 查询）：DEP-ADD、DEP-REMOVE、DEP-VERSION-CHANGE（package.json 说明符）、DEP-SOURCE-CHANGE（registry/git URL/local 分类变化）、DEP-LOCK-ADD、DEP-LOCK-REMOVE、DEP-LOCK-VERSION-CHANGE（pnpm v9 importers 直接依赖解析版本）、DEP-LOCK-MISSING（声明存在而 lock 缺失）、DEP-MANIFEST-INVALID（损坏 JSON/YAML，证据锚定解析错误行）。缺失锁文件记 `no_lockfile:not_checked`，非 9.x lockfileVersion 记 `lockfile_version_unsupported:*`，均不伪装已检查。依赖文件仅经白名单选择用途（仓库根 package.json / pnpm-lock.yaml，复用路径/私有/凭据/预算检查）。

一致性（`rules/consistency.py`；仅可验证声明）：CONS-PARAM-EXTRA（JSDoc 多余参数）、CONS-PARAM-MISSING（实现参数未声明）、CONS-PARAM-TYPE-MISMATCH、CONS-RETURN-TYPE-MISMATCH（仅双方均可确定时比较）、CONS-PURITY-MISMATCH（纯函数声明白名单措辞 vs 行为边）。一致 → consistent 记录不产 Finding；缺声明/含糊自然语言 → unknown 保留原因。纯函数声明措辞白名单：`no side effects`、`no side-effects`、`pure function`、`无副作用`、`纯函数`。

变更分类基于方法规范化函数体指纹（去空行/行尾空白）+ diff rename 映射对齐；仅插入空行不改变 existing/new 分类。影响路径候选传播：路径含 candidate 边整体降为 candidate；无路径记 `no_path_in_graph:not_a_safety_claim`。

### 一次性交付与验证

新增 `engine/tests/test_analysis_slice.py` 作为真实 Git→结果集成入口；可新增相关测试文件。集成测试运行现有 fixture，并补充临时仓库的删除/重命名/依赖变化与失败场景；不能仅注入构造好的图来宣称完整链路通过。输入固定使用 manifest 里的提交 SHA。

最终自动验证（每条分别保存退出码；只在相关代码再次变化时重跑）：

```powershell
docker compose exec -T daemon pytest -q -o 'addopts=-p no:cacheprovider'
docker compose exec -T web pnpm exec tsc --noEmit
```

依赖/镜像改变才先执行 `docker compose up --build -d daemon`；服务未运行时按已有 Compose 配置启动。全套 pytest 必须包含新增集成及安全回归，不能以测试数量达标代替矩阵验证。运行输出存 PRIVATE/verification/<日期>-batch03/。

交付一份简洁回执：入口与示例命令、矩阵→测试→代码映射、actual modified files、命令/退出码/原始输出位置、契约是否变化、剩余限制、状态 TESTED。不逐 Task 提交回执，不宣称 Codex PASSED；完成后停止。

### 已发现的计划冲突及处理

- 原 SEC-001 Depends On 含 LLM-001/API-001，但这两项位于未来 Batch-06/04。本批只验收已存在分析边界；API/模型安全在对应批次验收，SEC-001 全量状态保持部分覆盖，不能为满足依赖提前开发未来模块。下方 SEC-001 同步分阶段定义。
- PRD 的 Evidence resolver 与当前冻结 EvidenceAnchor 字段不同：本批通过内部定位结果保存完整状态，成功 anchor 沿用现有字段；不得静默丢弃 resolver，也不得自行改写冻结模型。对外聚合冻结需在后续批次处理。
- 选择器默认 TS/JS 与依赖分析读 JSON/YAML 的冲突按上文用途白名单处理。保留路径安全优先级，不能通过关闭排除规则解决。

## OPS-000 — 建立 daemon Docker 测试骨架

**Why**：第一批必须能在规定环境中产生真实可审计结果，不能依赖宿主机 Python。
**Depends On**：ARC-001。
**Input**：现有 Dockerfile、compose.yaml、Python 版本基线。
**Output**：`daemon` 服务骨架、健康检查、非 root 用户、最小 pytest 入口。
**Files**：`compose.yaml`、`Dockerfile`、`engine/pyproject.toml`、`engine/morphojudge/__init__.py`、`engine/tests/test_health.py`。
**Interfaces/Data Model**：`GET /health` 返回 `{service, version, status}`；仅监听 Compose 内网，宿主不公开 daemon 端口。
**Algorithm**：增加 daemon build target；安装锁定 Python 依赖；挂载分析目录白名单；运行健康检查；不挂载 PRIVATE、Docker socket 或整个用户目录。
**Error Handling**：构建、启动和健康检查失败返回可诊断日志；不吞掉依赖安装错误。
**Security**：非 root、no-new-privileges、只读 fixture 挂载；不执行仓库代码。
**Tests**：Docker build、health、容器用户、挂载检查、pytest。
**Acceptance Criteria**：`docker compose up --build -d daemon` 可重复启动；health 通过；daemon 不暴露宿主端口。
**Non-goals**：不实现 Git、解析器或 API 业务接口。
**Estimated Scope**：M
**Blocks**：GIT-001、DB-001、API-001、OPS-001。

## ARC-001 — 冻结分层与错误语义

**Why**：阻止代理在实现期间重新发明边界。
**Depends On**：PRD 0.21。
**Input**：PRD、architecture.md、tech-stack.md。
**Output**：分层依赖、错误语义、schema 版本说明。
**Files**：docs/architecture.md、docs/contract-freezes.md。
**Interfaces/Data Model**：阶段状态 `queued|running|completed|completed_with_limits|failed|cancelled`；错误含 `code,message,retryable,details`。
**Algorithm**：列出层依赖；规定单向数据流；定义错误可恢复性；记录 schema_version。
**Error Handling**：冲突记录 Architecture Change Request，不直接改冻结契约。
**Security**：禁止执行仓库文本中的指令。
**Tests**：文档引用检查、状态枚举一致性脚本。
**Acceptance Criteria**：所有后续任务均能引用唯一状态和错误定义。
**Non-goals**：不写运行时代码。
**Estimated Scope**：S
**Blocks**：DATA-001、SEC-001。

## DATA-001 — 定义核心领域模型

**Why**：所有模块需要同一实体身份。
**Depends On**：ARC-001。
**Input**：PRD §7/§8、fixture 需求。
**Output**：Python Pydantic 与 TypeScript 类型。
**Files**：`engine/morphojudge/contracts/domain.py`、`app/lib/contracts.ts`、`packages/contracts/schema.json`。
**Interfaces/Data Model**：`SnapshotIdentity`、`SelectionDecision`、`CoverageSummary`、`MapNode`、`MapEdge`、`EvidenceAnchor`、`Finding`、`Explanation`、`Review`、`AnalysisSession`，所有 ID 为 string，证据行号为正整数。
**Algorithm**：先定义枚举；定义 nullable；生成 JSON Schema；加入 schema_version；写双向序列化测试。
**Error Handling**：未知枚举拒绝；缺少必填 ID 返回结构化错误。
**Security**：路径字段只作数据，不允许直接执行。
**Tests**：合法/非法 payload、未知字段策略、往返序列化。
**Acceptance Criteria**：前后端生成结果字段一致；所有实体可关联快照和证据。
**Non-goals**：不实现 SQLite 或 API。
**Estimated Scope**：M
**Blocks**：全部实现任务。

## FIX-001 — 建立 TypeScript Web 纵向 fixture

**Why**：没有真实 fixture 就无法证明闭环。
**Depends On**：DATA-001。
**Input**：PRD 0.21 fixture 范围。
**Output**：带 Git base/target 的脱敏 TypeScript 仓库。
**Files**：`tests/fixtures/ts-web/`、`tests/fixtures/manifests/`。
**Interfaces/Data Model**：manifest 显式记录页面事件和人工确认的功能映射。
**Algorithm**：创建路由；页面按钮触发方法；方法调用 API；API 读写数据库；加入 fetch、child_process、fs 和权限线索；提交 base/target 差异。
**Error Handling**：加入动态 import、别名、解析失败和删除/重命名样例。
**Security**：fixture 脚本不能被分析器执行。
**Tests**：Git 提交一致性和预期行号快照。
**Acceptance Criteria**：所有必需实体和至少一组行为风险可被静态识别。
**Non-goals**：不追求真实业务完整性。
**Estimated Scope**：M
**Blocks**：GIT-001、PARSE-001、TEST-001。

## GIT-001 — 实现 Snapshot Identity

**Why**：分析必须绑定不可变输入。
**Depends On**：DATA-001、FIX-001。
**Input**：登记仓库绝对路径、base ref、target ref。
**Output**：`SnapshotIdentity {repository_id, canonical_path, base_commit, target_commit, snapshot_id}`。
**Files**：`engine/morphojudge/git/snapshot.py`、`engine/tests/test_snapshot.py`。
**Algorithm**：归一化路径；确认目录为 Git 仓库；使用安全参数读取 refs；解析 commit SHA；对输入和规则版本计算 snapshot_id；读取 dirty 状态但不执行 hooks。
**Error Handling**：不存在、ref 无效、权限拒绝、非仓库、子模块/LFS/dirty workspace 分别返回错误或限制状态。
**Security**：拒绝路径穿越、符号链接逃逸和 shell 拼接；使用参数数组。
**Tests**：合法 ref、分离 HEAD、无效 ref、dirty、symlink、submodule、binary。
**Acceptance Criteria**：同一输入生成相同 ID；不同 commit 生成不同 ID；不运行目标代码。
**Non-goals**：不解析源码。
**Estimated Scope**：M
**Blocks**：GIT-002、SEL-001。

## GIT-002 — 实现 Diff 与文件状态

**Why**：提供新增/修改/删除/重命名输入。
**Depends On**：GIT-001。
**Input**：SnapshotIdentity。
**Output**：`ChangedFile {path,status,old_path,additions,deletions,binary}`。
**Files**：`engine/morphojudge/git/diff.py`、测试。
**Algorithm**：只读 Git diff；解析 rename/copy；记录 binary 和未跟踪策略；生成 old/new 行映射。
**Error Handling**：无法读取对象进入 failed；binary 进入 limited。
**Security**：禁止 Git 参数注入和外部命令扩展。
**Tests**：新增、修改、删除、重命名、binary、未跟踪。
**Acceptance Criteria**：每个变更文件可定位到 base/target。
**Non-goals**：不做语义解析。
**Estimated Scope**：M
**Blocks**：SEL-001、EVD-001。

## SEL-001 — 实现 SelectionDecision 与 Coverage

**Why**：明确分析覆盖边界。
**Depends On**：GIT-002、DATA-001。
**Input**：ChangedFile、语言规则、大小预算、路径边界。
**Output**：SelectionDecision 列表和 CoverageSummary。
**Files**：`engine/morphojudge/selection/service.py`、测试。
**Algorithm**：逐文件判断支持语言、路径、binary、大小和预算；记录 selected/excluded/limited 及 reason/rule_id；汇总计数。
**Error Handling**：单文件失败不吞掉；整体只在无可分析文件时 failed。
**Security**：排除 PRIVATE、凭据和越界路径；不读取未选文件内容。
**Tests**：未知语言、超大文件、PRIVATE、symlink、预算截断。
**Acceptance Criteria**：预览和实际运行复用同一决策；未检查不显示为安全。
**Non-goals**：不调用模型。
**Estimated Scope**：M
**Blocks**：PARSE-001、DB-001。

## PARSE-001 — TypeScript/JavaScript Tree-sitter 文件解析

**Why**：建立语法和位置事实。
**Depends On**：SEL-001。
**Input**：selected 文件内容。
**Output**：解析树、语法错误、节点位置。
**Files**：`engine/morphojudge/parser/typescript.py`、测试。
**Algorithm**：按扩展名选择 grammar；解析 UTF-8；收集 error node；保留 byte/line/column；不执行模块。
**Error Handling**：编码错误、语法错误、超时进入 parse error/limited。
**Security**：源码按文本处理，任何注释都不产生指令。
**Tests**：合法 TS/JS、JSX、语法错误、Unicode、超大文件。
**Acceptance Criteria**：每个 symbol 有文件和起止位置；错误可回溯。
**Non-goals**：不构建完整类型系统。
**Estimated Scope**：M
**Blocks**：PARSE-002、EVD-001。

## PARSE-002 — Symbol/Route/Event/Contract 提取

**Why**：生成页面、功能候选、方法和契约实体。
**Depends On**：PARSE-001、FIX-001。
**Input**：语法树、fixture manifest。
**Output**：实体草稿和来源。
**Files**：`engine/morphojudge/parser/extract.py`、测试。
**Algorithm**：提取函数/类方法；识别 Next/React 路由和事件；读取 interface/type/OpenAPI/Zod；加载人工功能映射；无映射标记 unmapped。
**Error Handling**：动态路由保留模式；无法识别的事件进入 candidate。
**Security**：manifest 仅作数据，不执行。
**Tests**：同名方法、别名、动态 route、显式映射和缺失映射。
**Acceptance Criteria**：功能语义不会仅由方法名生成。
**Non-goals**：不生成模型说明。
**Estimated Scope**：L
**Blocks**：REL-001、LLM-002。

## PARSE-003 — 调用与数据操作提取

**Why**：支持方法→契约→数据库/行为追踪。
**Depends On**：PARSE-002。
**Input**：实体、AST、import 索引。
**Output**：calls/reads/writes/sends 边。
**Files**：`engine/morphojudge/parser/relations.py`、测试。
**Algorithm**：解析本地 import、别名、直接调用、SQL、fetch/fs/child_process；无法求值的动态目标为 candidate/unresolved。
**Error Handling**：跨文件缺失目标保留 unresolved；不因单边失败丢弃节点。
**Security**：不导入或执行依赖。
**Tests**：别名、循环、动态 import、SQL 拼接、第三方调用。
**Acceptance Criteria**：边带 source、location、resolver、snapshot。
**Non-goals**：不承诺运行时 trace。
**Estimated Scope**：L
**Blocks**：REL-001、BEH-001。

## REL-001 — 构建 SoftwareMap 与邻接查询

**Why**：统一前端图、联动和影响查询。
**Depends On**：PARSE-003、DATA-001。
**Input**：实体和边草稿。
**Output**：SoftwareMap、正向/反向邻接索引。
**Files**：`engine/morphojudge/relations/graph.py`、测试。
**Algorithm**：校验端点；按 relation 建邻接；BFS/DFS 使用 visited；应用节点/深度预算；输出 truncated 和 limits。
**Error Handling**：孤立节点保留；坏边进入 unresolved/error 记录。
**Security**：图查询不可读取路径或调用工具。
**Tests**：共享方法、调用环、候选边、反向影响、预算截断。
**Acceptance Criteria**：图与表可指向同一来源；复杂度受预算约束。
**Non-goals**：不引入图数据库。
**Estimated Scope**：M
**Blocks**：ANL-001、API-002。

## BEH-001 — 行为规则

**Why**：发现网络、Shell、文件、权限线索。
**Depends On**：PARSE-003、GIT-002。
**Input**：AST/边、base/target。
**Output**：Finding 草稿和 EvidenceAnchor。
**Files**：`engine/morphojudge/rules/behavior.py`、测试。
**Algorithm**：匹配调用和参数来源；标记 baseline/existing/new/deleted；动态目标为 candidate/unresolved；不判断实际执行。
**Error Handling**：未知配置进入 unknown/limited。
**Security**：规则只读输入。
**Tests**：fetch、child_process、fs、权限、合法请求反例。
**Acceptance Criteria**：行为线索不写成实际行为或漏洞结论。
**Non-goals**：不调用模型。
**Estimated Scope**：M
**Blocks**：ANL-001。

## DEP-001 — 依赖与一致性规则

**Why**：识别依赖变化和声明实现差异。
**Depends On**：GIT-002、PARSE-002。
**Input**：package.json/lockfile、MethodFacts。
**Output**：Finding/Evidence。
**Files**：`engine/morphojudge/rules/dependency.py`、`rules/consistency.py`、测试。
**Algorithm**：比较依赖名称/版本/来源；比较参数、返回字段、副作用声明；缺失声明为 unknown。
**Error Handling**：锁文件解析失败进入 limited。
**Security**：不安装依赖、不访问 registry。
**Tests**：新增包、Git URL、锁文件冲突、参数/返回/副作用差异。
**Acceptance Criteria**：规则 ID 稳定、证据可定位。
**Non-goals**：不做漏洞情报查询。
**Estimated Scope**：M
**Blocks**：ANL-001。

## EVD-001 — Evidence Resolver

**Why**：所有发现和解释共享证据格式。
**Depends On**：GIT-002、PARSE-001、DATA-001。
**Input**：snapshot、path、old/new location、rule。
**Output**：EvidenceAnchor。
**Files**：`engine/morphojudge/evidence/resolver.py`、测试。
**Algorithm**：验证路径属于快照；校验行号；映射 Diff old/new；截取安全片段；记录 resolver 状态和限制。
**Error Handling**：越界/文件消失为 unresolved；不返回近似“准确”位置。
**Security**：路径 containment、symlink、二进制和 HTML 文本转义。
**Tests**：新增/删除/重命名行映射、越界、Unicode、binary。
**Acceptance Criteria**：Evidence 可独立回到文件、提交、行号和规则。
**Non-goals**：不由模型决定位置。
**Estimated Scope**：L
**Blocks**：ANL-001、LLM-001、RPT-001。

## ANL-001 — Finding 与影响分析

**Why**：把关系和规则结果组织成可复核发现。
**Depends On**：REL-001、BEH-001、DEP-001、EVD-001。
**Input**：SoftwareMap、Evidence、Coverage。
**Output**：Finding、RelationPath、影响节点。
**Files**：`engine/morphojudge/analyzer/service.py`、测试。
**Algorithm**：合并同一证据的规则结果；沿反向边计算候选影响；保留 resolution；绑定 evidence_ids；生成稳定 finding_id。
**Error Handling**：无证据的结果丢弃并记录 analyzer error；部分图允许 partial。
**Security**：不提升候选为 resolved。
**Tests**：同一方法多页面、候选路径、无影响和受限路径。
**Acceptance Criteria**：每个 Finding 至少有一个 Evidence 或明确 unresolved 原因。
**Non-goals**：不生成模型文本。
**Estimated Scope**：M
**Blocks**：DB-001、API-002。

## DB-001 — SQLite schema 与迁移

**Why**：保存可恢复、可复核结果。
**Depends On**：DATA-001、ANL-001。
**Input**：Analysis、Coverage、Map、Finding、Evidence。
**Output**：迁移版本、表、索引和事务 repository。
**Files**：`engine/morphojudge/db/migrations/`、`repository.py`、测试。
**Algorithm**：建立 schema_version；外键约束；按 snapshot_id、path、finding_id、evidence_id 建索引；单阶段事务提交。
**Error Handling**：迁移失败停止写入并保留旧库；事务回滚。
**Security**：数据库路径限制在本地数据目录；不保存密钥和完整私有代码日志。
**Tests**：空库迁移、重复迁移、回滚、并发读、唯一键。
**Acceptance Criteria**：重启可恢复；同一 snapshot 不覆盖旧结果。
**Non-goals**：不引入 ORM 必要复杂度。
**Estimated Scope**：M
**Blocks**：ANL-003、API-001。

## API-001 — FastAPI 分析任务接口

**Why**：让 Web 控制分析而不承担分析逻辑。
**Depends On**：GIT-001、DB-001。
**Input**：结构化 repository/base/target/options。
**Output**：创建、状态、取消、错误响应。
**Files**：`engine/morphojudge/api/routes/analyses.py`、测试。
**Algorithm**：校验请求；生成 idempotency key；创建 session；后台 worker 轮询；返回统一 error schema。
**Error Handling**：400 输入、404 资源、409 重复、422 schema、500 内部；任务失败保留阶段。
**Security**：只接受登记仓库 ID，不接受任意命令；localhost 监听。
**Tests**：创建、重复、取消、失败、恢复、并发请求。
**Acceptance Criteria**：API 状态与 SQLite 一致。
**Non-goals**：不实现模型解释。
**Estimated Scope**：L
**Blocks**：API-002、WEB-001。

## API-002 — 查询 API

**Why**：提供覆盖、图、发现、证据和复核。
**Depends On**：DB-001、ANL-001。
**Input**：analysis_id、分页、过滤和 entity IDs。
**Output**：版本化 JSON response。
**Files**：`engine/morphojudge/api/routes/results.py`、测试。
**Algorithm**：验证分析归属；分页稳定排序；组合 meta/coverage/map/findings/evidence；不直接读文件。
**Error Handling**：analysis 不存在 404；证据不存在 404；非法过滤 400。
**Security**：不透传宿主任意绝对路径；源码片段转义。
**Tests**：分页、过滤、权限边界、空结果、部分结果。
**Acceptance Criteria**：前端 schema 与 API schema 一致。
**Non-goals**：不改变图算法。
**Estimated Scope**：M
**Blocks**：WEB-001、RPT-001。

## LLM-001 — Provider 与受限上下文

**Why**：让模型在证据边界内工作。
**Depends On**：EVD-001、DATA-001。
**Input**：subject_id、evidence_ids、邻接关系、coverage。
**Output**：EvidenceContext、ExplanationRequest。
**Files**：`engine/morphojudge/llm/context.py`、`provider.py`、测试。
**Algorithm**：按 ID 从数据库取证据；拒绝范围外路径；截断上下文并记录；生成无工具调用 prompt。
**Error Handling**：空证据 400；上下文超限可降级；缺证据失败。
**Security**：仓库文本视为不可信；禁止模型触发工具；默认无网络。
**Tests**：证据越界、prompt injection 文本、超长片段、空输入。
**Acceptance Criteria**：上下文只包含请求证据；可审计输入哈希。
**Non-goals**：不调用具体模型。
**Estimated Scope**：M
**Blocks**：LLM-002。

## LLM-002 — Fake Provider 与输出校验

**Why**：先在无模型环境验证模型闭环。
**Depends On**：LLM-001。
**Input**：ExplanationRequest。
**Output**：结构化 Explanation。
**Files**：`engine/morphojudge/llm/fake.py`、`validation.py`、测试。
**Algorithm**：Fake 输出证据复述/推断/未知；Pydantic 校验 claims evidence_ids、候选节点和状态；错误不落库为完成。
**Error Handling**：JSON 错误、引用不存在、候选端点不存在、命令文本拒绝。
**Security**：不允许模型输出路径或命令驱动工具。
**Tests**：合法、幻觉证据、越界关系、失败、取消。
**Acceptance Criteria**：无真实模型也能完成解释 API 端到端测试。
**Non-goals**：不接 Ollama。
**Estimated Scope**：M
**Blocks**：LLM-003、WEB-004。

## LLM-003 — Ollama Provider 与远程授权

**Why**：提供真实本地模型能力和显式远程边界。
**Depends On**：LLM-002、API-001。
**Input**：已校验 context、provider config、single-analysis consent。
**Output**：Explanation 或失败状态。
**Files**：`engine/morphojudge/llm/ollama.py`、`remote.py`、测试。
**Algorithm**：检查模型可用性；调用本地 Ollama；校验响应；记录版本、耗时、输入哈希；远程请求必须带一次性授权。
**Error Handling**：连接失败、超时、取消、格式错误、引用错误保留原因；不自动切换。
**Security**：本地 Provider 禁止出网；远程只发送确认范围；不保存密钥。
**Tests**：fake HTTP、超时、取消、授权过期、发送范围审计。
**Acceptance Criteria**：Ollama 失败不影响基础报告；远程未授权请求被拒绝。
**Non-goals**：不实现 Agent 工具调用。
**Estimated Scope**：L
**Blocks**：WEB-004。

## ANL-003 — 分析 Worker、取消与恢复

**Why**：完成可重跑异步分析。
**Depends On**：DB-001、API-001、SEL-001、ANL-001。
**Input**：AnalysisSession。
**Output**：阶段结果和最终状态。
**Files**：`engine/morphojudge/worker/analysis.py`、测试。
**Algorithm**：按 git→selection→parse→rules→persist 顺序；每阶段事务提交 checkpoint；取消标记；重启从最后 checkpoint 恢复。
**Error Handling**：单文件错误进入 coverage；阶段不可恢复错误 failed；partial 为 completed_with_limits。
**Security**：worker 不运行目标命令。
**Tests**：中途崩溃恢复、取消、重试、预算、并发 session。
**Acceptance Criteria**：重启不丢已提交结果，不重复写入旧 snapshot。
**Non-goals**：不做分布式队列。
**Estimated Scope**：L
**Blocks**：WEB-001、ACC-001。

## WEB-001 — 新建分析与状态接入

**Why**：让当前 UI 创建真实任务。
**Depends On**：API-001、ANL-003。
**Input**：现有 Setup 表单。
**Output**：analysis_id 和轮询状态。
**Files**：`app/page.tsx`、`app/lib/api.ts`、`app/lib/schemas.ts`。
**Algorithm**：提交结构化请求；保留表单；轮询；映射 loading/error/partial；成功进入当前报告/导览。
**Error Handling**：网络失败可重试；不清空用户输入；失败不显示完成。
**Security**：浏览器不直读仓库，不传任意命令。
**Tests**：Playwright 创建、失败、取消、恢复。
**Acceptance Criteria**：视觉基线不漂移。
**Non-goals**：不改导航和布局。
**Estimated Scope**：M
**Blocks**：WEB-002。

## WEB-002 — SoftwareMap 真实 API 适配

**Why**：替换 fixture 数据源。
**Depends On**：API-002、REL-001。
**Input**：analysis_id、当前视图状态。
**Output**：现有 SoftwareWorkspace 数据。
**Files**：`app/software-map/workspace.tsx`、`linked-columns.tsx`、`app/lib/api.ts`。
**Algorithm**：请求 map/meta；保留 fixture 示例模式；成功替换数据；选择失效时清空下游；错误提供重试。
**Error Handling**：空图、partial、API schema mismatch、404。
**Security**：不在客户端执行源码；片段作为文本渲染。
**Tests**：真实 fixture Playwright、切换范围、方法表、图和详情。
**Acceptance Criteria**：现有视觉和交互保持；真实节点来自 API。
**Non-goals**：不重构页面。
**Estimated Scope**：L
**Blocks**：WEB-003、WEB-004。

## WEB-003 — 覆盖、Finding 与证据详情接入

**Why**：完成事实到证据的 UI 闭环。
**Depends On**：API-002、EVD-001、ANL-001。
**Input**：coverage/findings/evidence。
**Output**：当前右侧详情、代码预览和影响路径真实化。
**Files**：`app/software-map/workspace.tsx`、报告组件。
**Algorithm**：按 node/finding 关联；显示解析/候选/未知/受限；证据跳转；保留翻转卡。
**Error Handling**：证据失效显示原因；不显示近似行号。
**Security**：源码 HTML 转义；不执行 markdown/HTML。
**Tests**：证据定位、删除文件、候选关系、未检查。
**Acceptance Criteria**：每个发现可回到同一 snapshot 的文件和行号。
**Non-goals**：不添加新主导航。
**Estimated Scope**：M
**Blocks**：WEB-004、RPT-001。

## WEB-004 — 本地模型解释 UI

**Why**：把本地模型能力接入当前右侧详情。
**Depends On**：LLM-002、LLM-003、WEB-003。
**Input**：selected node/finding、evidence IDs。
**Output**：解释、候选关系、模型状态。
**Files**：`app/software-map/workspace.tsx`、`app/api/explanations/route.ts`、模型组件。
**Algorithm**：按需请求；显示 provider/model/status；校验引用；候选关系只读；失败重试；远程授权确认。
**Error Handling**：模型不可用仍可浏览；切换节点清理旧解释；远程拒绝不影响报告。
**Security**：默认本地；授权范围清晰；不允许模型工具调用。
**Tests**：Fake、Ollama mock、失败、超时、远程授权、串线。
**Acceptance Criteria**：模型结果与事实、规则、人工状态分层显示。
**Non-goals**：不让模型修改图或自动批准。
**Estimated Scope**：M
**Blocks**：RPT-001、ACC-001。

## RPT-001 — 报告与导出

**Why**：形成可复核交付物。
**Depends On**：API-002、WEB-003、WEB-004。
**Input**：同一 SQLite 结果视图。
**Output**：Markdown/JSON 报告。
**Files**：`engine/morphojudge/report/`、报告 Web 组件、测试。
**Algorithm**：固定顺序输出输入、coverage、findings、evidence、explanations、reviews、limits；稳定排序；JSON schema 版本化。
**Error Handling**：partial 明示；缺模型解释不阻塞导出；证据缺失标 unresolved。
**Security**：报告不写密钥；源码片段按本地权限保存。
**Tests**：fixture golden、Markdown/JSON 一致性、空结果、失败分析。
**Acceptance Criteria**：报告事实与 UI/API 一致。
**Non-goals**：不生成安全证明。
**Estimated Scope**：M
**Blocks**：ACC-001。

## TEST-001 — 分析规则与契约测试套件

**Why**：防止只测 mock。
**Depends On**：FIX-001、PARSE-003、ANL-001。
**Input**：真实 fixture、反例和边界样例。
**Output**：pytest/Vitest 测试和 golden 数据。
**Files**：`engine/tests/`、`tests/`。
**Algorithm**：按层测试；断言实体、边、证据、状态和限制；禁止修改断言掩盖回归。
**Error Handling**：失败输出最小复现和阶段。
**Security**：测试验证不执行仓库脚本。
**Tests**：覆盖任务要求中的所有 fixture 场景。
**Acceptance Criteria**：基础测试在 Docker 通过。
**Non-goals**：不调用真实云模型。
**Estimated Scope**：M
**Blocks**：ACC-001。

## TEST-002 — Playwright 用户闭环测试

**Why**：证明当前原型真实可用。
**Depends On**：WEB-001、WEB-002、WEB-003、WEB-004、RPT-001。
**Input**：Docker Web + fixture daemon。
**Output**：用户任务测试和截图基线。
**Files**：`tests/e2e/`。
**Algorithm**：创建分析→查看图→方法→证据→模型解释→复核→导出；覆盖 390/768/1136/1440。
**Error Handling**：验证失败/重试/取消路径。
**Security**：确认浏览器不访问任意本地路径。
**Tests**：页面级溢出、键盘、触屏、状态恢复。
**Acceptance Criteria**：全链路不依赖 fixture 直接注入页面状态。
**Non-goals**：不做性能压测。
**Estimated Scope**：M
**Blocks**：ACC-001。

## SEC-001 — 安全边界验收

**Why**：Local First 的可信前提。
**Depends On**：Batch-03 分析边界验收依赖 GIT-001、SEL-001、ANL-001；API 安全子集在 API-001 完成后验收，模型安全子集在 LLM-001 完成后验收。后两项不是 Batch-03 前置条件。
**Input**：恶意路径、symlink、仓库 prompt injection、远程配置。
**Output**：安全测试和修复清单。
**Files**：`engine/tests/security/`、`tests/e2e/security/`。
**Algorithm**：尝试路径穿越、命令注入、symlink 逃逸、未授权远程调用、模型工具调用和 PRIVATE 泄露。
**Error Handling**：每个拒绝有稳定错误码和日志。
**Security**：不把攻击样例复制进公开报告。
**Tests**：Batch-03 为分析/路径/命令/内容边界；后续 API/模型批次补其 A6 子集，最终 ACC-001 汇总全部结果。批次回执明确已检查和未到期范围，不把部分覆盖写成 SEC-001 全量完成。
**Acceptance Criteria**：攻击输入不能执行目标代码、任意命令或外发源码。
**Non-goals**：不宣称形式化安全证明。
**Estimated Scope**：M
**Blocks**：ACC-001。

## OPS-001 — Docker 纵向环境

**Why**：统一可重复施工环境。
**Depends On**：API-001、DB-001。
**Input**：Compose、Dockerfile、锁文件。
**Output**：web/daemon/preview 开发和测试服务。
**Files**：`compose.yaml`、`Dockerfile`、`.dockerignore`。
**Algorithm**：分阶段镜像；非 root；只读仓库挂载；不挂 Docker socket/PRIVATE；健康检查；服务仅回环发布。
**Error Handling**：服务启动失败可诊断；不清理其他项目资源。
**Security**：容器最小权限和挂载白名单。
**Tests**：build、health、容器内 tsc/pytest、断网基础分析。
**Acceptance Criteria**：命令按 PRD §6.1 可重复运行。
**Non-goals**：不做生产部署。
**Estimated Scope**：M
**Blocks**：ACC-001。

## ACC-001 — Codex 端到端发布验收

**Why**：独立确认项目目标达成。
**Depends On**：全部 V0.1 任务、TEST-002、SEC-001、OPS-001。
**Input**：PRD、任务、真实 fixture、运行环境。
**Output**：验收报告和通过/失败状态。
**Files**：`docs/codex-acceptance.md`、验收产物目录。
**Algorithm**：执行 A0-A7；逐项建立 Task→PRD→Code→Test traceability；独立复核源码和测试。
**Error Handling**：失败任务标记 FIX_REQUIRED；禁止 Coding AI 自报通过。
**Security**：单独检查外发、执行和路径边界。
**Tests**：Docker、pytest、Vitest、Playwright、golden report。
**Acceptance Criteria**：所有发布门槛通过；限制完整记录；无伪造完成状态。
**Non-goals**：不扩大 v0.1 范围。
**Estimated Scope**：L
**Blocks**：V0.1 release。

