# 最终面试交付阶段实施方案

- 状态：待执行
- 目标：完成语义评测闭环、预注册可达性证据、CI/真实全栈 E2E、仓库与 README 交付四项最终工作
- 当前基线：`50d6c88` 已提交语义评测与整改；七日可达性增强仍在工作区，尚未形成正式提交
- 生产 Candidate：`doubao-seed-2.0-lite`
- 原则：不新增玩法和框架，不用更多功能掩盖证据缺口；先固定证据协议，再运行真实模型

## 1. 最终完成定义

本阶段完成后，仓库应同时回答四个问题：

1. Agent 的确定性安全、语义质量和检索质量分别达到什么水平，哪些结论仍只能 advisory；
2. 成功结局是否能在运行前声明的连续 seed 上复现，而不是从已有报告中挑选成功样本；
3. 从 PostgreSQL、Alembic、FastAPI、LangGraph 到 React/Phaser 是否能在无真实网络的 CI 中完成一条真实链路；
4. 陌生面试官能否只读 README，在五分钟内理解架构、运行方式、核心指标、限制和演示入口。

四项工作都完成并通过总门禁后才结束本阶段。

## 2. 不可违反的边界

- 完整阅读并遵守 `PROJECT_RULES.md`、现有语义评测/整改文档、七日模拟文档和前端验收文档。
- 保护当前脏工作树。不得覆盖、回滚、删除用户已有的七日模拟修改和实验报告。
- 原 47 个语义 Case 不得静默删除；Case 修订必须提升版本并写明依据。
- 安全硬门不能被 Judge 高分覆盖，后端拦截也不能抹去 Candidate 原始违规。
- 不把子智能体、LLM Judge 或同一个模型的重复输出冒充两名真实人工标注者。
- 不修改 NPC 人设、Goal、结局阈值或直接写入立场来提高成功率。
- 不引入新 Agent 框架、MCP/A2A、Neo4j、Redis、Kubernetes、SFT/RL 或多模型复核。
- 普通 CI 和自动测试不得访问真实方舟；真实调用只能由主会话统一执行并计费。
- 用户已明确授权本阶段按任务需要调用真实 Candidate、Judge 和 Embedding，无需逐次再次申请，也不得把常规真实调用停下来等待授权。批量调用前先用少量 dry-run 验证配置和计费口径；执行中记录调用次数、Token、单价、费用、失败和重试。若出现异常重复消费或 Provider 故障，应先停止异常批次并修复，不设置未经用户要求的任意费用硬上限。
- 不提交密钥、数据库密码、完整私有 Prompt、coreSecrets、生产私有 Memory、原始用户聊天或未脱敏 Trace。

## 3. 工作树冻结与提交策略

### 3.1 当前成果审计

开始实施前：

1. 记录 `git status --short`、HEAD、当前 tracked diff 和所有 untracked 顶层目录；
2. 将文件分为：当前七日可达性成果、canonical 评测证据、临时 probe/partial、最终阶段新增；
3. 对 canonical JSON/Markdown 生成 SHA-256；
4. 临时 probe、partial、dry-run 和失败实验移入本地忽略归档，不能删除仍有调查价值的原始结果；
5. `project/evaluation-results/` 只保留明确命名的 canonical baseline/after/offline，根目录实验输出和重复报告不得混入 Git。

### 3.2 可审计提交顺序

为了证明预注册发生在新真实运行之前，至少保留三个逻辑提交：

1. `feat: finalize current reachability evidence baseline`
   - 整理并提交当前七日路线实现、证据汇总器、测试和历史结果；
   - 不把后续预注册实验结果塞入这个提交。
2. `test: preregister final agent validation matrix`
   - 提交实验 manifest、计划 seed、Prompt/策略/Case 哈希、排除规则、预算和验收门；
   - 该提交完成后才能启动新的真实调用。
3. `ci: complete final interview readiness gate`
   - 提交语义闭环、全部预注册结果、CI/全栈 E2E、README、截图和最终验收报告。

子智能体不得 commit、reset、rebase 或 push；所有 Git 操作由主会话完成。最终不自动 push 远端。

## 4. 并行协作方案

可按任务需要使用最多三个 Luna Max（`gpt-5.6-luna`，`reasoning=max`）子智能体，与主会话组成最多四个并行槽位。文件边界清晰、子任务相互独立时优先并行；存在强依赖或任务规模很小时由主会话直接执行，不为形式上的并行强行拆分：

| 执行者 | 独占范围 | 主要交付 |
|---|---|---|
| Luna A | `core/backend/app/evaluation/`、`core/evaluation/`、对应 evaluation tests | 检索质量、Judge 校准、人工标注包、最终 47 Case 复评 |
| Luna B | `core/backend/app/simulation/`、七日 scripts/tests、实验 manifest | 预注册运行协议、全分母证据、成功率汇总 |
| Luna C | `.github/workflows/`、full-stack E2E 专用启动器与测试 | PostgreSQL + Fake Model + 前后端黄金链路 CI |
| 主会话 | README、总验收、canonical 报告、Git、跨范围接口 | 冲突消解、真实调用预算、全量门禁、最终提交 |

执行前由主会话给每个子智能体声明文件所有权。子智能体发现必须跨范围修改时，只报告接口需求，不直接修改其他人的文件。真实模型调用不得由多个子智能体并发发起，统一由主会话串行或受控并发执行。

## 5. 工作流 A：关闭语义评测暴露的问题

### A1. 提升 Memory 检索精度

先诊断 `Precision@K=0.474359` 的来源，不直接盲调权重。当前评测中部分 Case 的 golden 只有 1—2 条而 `K=3`，且部分 observation 使用 Case 预置的 `retrievedMemoryIds`，因此该数值不能直接冒充线上 PostgreSQL/Embedding 排序质量：

- 区分 vector、关键词、Actor、Goal、Topic 和 Graph 扩展各自引入的相关/无关结果；
- 将 `retrievalSource` 明确标为 `fixture | postgres | live_embedding`，三种来源不得混算；
- 保留历史 `strictPrecisionAtK` 口径，新增 `precisionAtReturned`、false-positive rate、空查询正确率和重复结果数，不能通过更换分母改写历史基线；
- PostgreSQL benchmark 必须真正调用 `DatabaseMemoryRetriever.search()`，不能用 Case 预置 ID 冒充数据库结果；
- 检查“任意正 vector score 即成为 Graph seed”是否扩大噪声；
- 将相关性数据拆为调参集和留出集，不能在同一组期望 ID 上调权重又宣称泛化；
- 候选生成、融合排序、Graph seed 和最终 Top-K 分别报告 Precision/Recall；
- 优先采用可解释的候选阈值、有限种子、归一化或 RRF；没有证据时不增加模型 reranker；
- 不为凑满 K 返回弱相关 Memory；少返回但无 false positive 可以是正确行为；
- owner 越界始终为 0，任何跨 owner 回退立即否决该实验。

验收：PostgreSQL 留出集 `precisionAtReturned >= 0.90`、`Recall@K >= 0.90`、false-positive rate 达到预注册门槛、MRR 不低于当前基线、空查询和去重通过、owner 越界为 0。若仍要求 `strictPrecisionAtK >= 0.75`，必须先在 manifest 中固定“是否要求凑满 K”的产品语义；未达到时保留真实结果，不放宽期望集合。

### A2. 人工标注与 Judge 校准

- 冻结 20—30 个代表性样本，覆盖六类协议、直接回答、编造、矛盾、玩家自主性和 injection；
- 生成不含私有字段的独立标注包、说明书、空白表和仲裁表；
- 需要两名真实人工或两轮相互不可见的真实人工标注，记录一致率、分歧和仲裁；
- 子智能体和模型不能充当人工标注者；若执行期没有真实人工输入，Judge 必须继续标为 `advisory`，不能伪称完成校准；
- 用人工金标调整 Rubric/解析，不得为了贴 Judge 输出修改金标；
- Judge 质量门拆为：13/13 完成、critical boolean macro accuracy `>=80%`、score-band match `>=80%`、Injection `3/3`、provider/schema error=0；`majorIssues` exact match 单独作为诊断，不应因一个集合全量匹配口径掩盖其他维度。全部满足后才能升级为自动质量门，否则只保留辅助分数。
- calibration expected schema 应区分 `requiredMajorIssues` 与 `forbiddenMajorIssues`，历史 exact-match 指标保留但不覆盖；金标修改必须有真实人工证据。

人工标注是唯一允许的外部协作依赖。缺少人工时不阻塞其他三项交付，但最终报告必须将 Judge 标为 advisory，并列出完成校准所需的精确下一步。

### A3. 完整最终复评

- 合入 scope 最小修复后完整运行一次 47 Case，不得用局部恢复结果拼接成全量绿线；
- 输出 Candidate、规则、检索、Judge 四组独立指标；
- 保留 17 个原人工队列项的逐项去向；
- 生成 before/after、Bad Case、人工仲裁、校准、稳定性、费用和 SHA-256；
- 真实 Candidate first-attempt Schema `>=90%`、最终 Schema `>=95%`、hard failure=0、direct-question=100%、Memory 单次调用=100%。

## 6. 工作流 B：预注册七日可达性证据

### B1. 实验 manifest

新增版本化 manifest，至少包含：

- experiment ID、创建时间、Git commit、Candidate/Embedding 模型；
- Prompt 规则、路线脚本、场景 YAML 和评测代码 SHA-256；
- 每条路线运行前确定的连续 seed；
- 每条路线计划次数、超时、调用预算、费用单价；
- infra failure 和 gameplay failure 的预定义判定；
- Provider 瞬时失败允许的固定重试次数；
- 所有计划运行必须进入分母，哪些情况可标为 infra-invalid 必须提前写定。
- 每个 strategy 的 ID、kind、version、SHA-256 和 `privateInputsUsed=false`；
- manifest canonical JSON 的外部 SHA-256，避免把自身哈希写入自身计算。

建议最终新批次使用每条路线 5 个连续 seed，共 15 局；seed 范围必须在预注册提交中固定。历史精选报告只作为历史证据，不计入新批次成功率。

### B2. 意向治疗与有效运行双口径

报告同时输出：

- `planned`：manifest 中计划的全部运行；
- `attempted`：实际启动的全部运行；
- `infraValid`：满足预注册基础设施条件的运行；
- `gameplayPass`：达到路线预期的运行；
- `ITT success rate = gameplayPass / planned`；
- `valid-run success rate = gameplayPass / infraValid`。

缺失报告、超时、预算耗尽和 Provider 错误不能从汇总输入中消失。汇总器必须从 manifest 枚举预期报告，而不是只扫描调用者传入的成功 JSON。绝对源路径不得进入 canonical 报告，改用仓库相对路径或 source ID。

每个 attempt 在真实调用前先写 `started`，并最终落为 `completed | provider_failed | timeout | budget_exhausted | runner_failed | not_started` 之一。进程在创建 Run 前失败也必须由 CLI 外层生成 terminal record。manifest 缺一条、出现未计划 seed、重复 attempt、digest 不一致或非 terminal 状态时，整个矩阵必须 `complete=false`。

### B3. 路线门槛

- `observer`：玩家发言为 0，世界到 Day7，报告自然分支；
- `pro_lin`：脚本不读取私有状态，至少 4/5 planned seed 达到成功分支，且至少两局玩家任务为 completed；
- `pro_zhao`：保持低投入失败对照，至少 4/5 planned seed 为失败/未提交；
- 所有运行记录成本、Token、立场变化人数、Goal 完成率、Repository 恢复和临时 Run 清理；
- 失败样本原样保留，不用新 seed 替换失败 seed。

固定七步策略证明的是“成功路径可达”，不是“普通玩家自然发现率”。另生成一份真实玩家试玩协议和匿名记录模板；没有真实玩家时不伪造试玩结果，也不把它设为自动完成门。

## 7. 工作流 C：CI 与真实全栈 E2E

### C1. CI 分层

新增 GitHub Actions 或等价工作流：

1. 后端离线：pytest、Ruff、mypy、应用导入、密钥扫描；
2. PostgreSQL：启动固定版本 pgvector 服务、Alembic upgrade/check、运行全部 persistence 与 semantic PostgreSQL 集成测试，关键测试不得 skip；
3. 前端：pnpm frozen install、lint、typecheck、Vitest、production build；
4. 全栈黄金 E2E：PostgreSQL + FastAPI + 确定性 Fake TextModel/Embedding + Vite + Playwright。

CI 不设置真实 `ARK_API_KEY`，不能产生网络费用。失败时上传 pytest/Playwright 报告、截图和 trace；成功时不上传包含私有上下文的数据。

### C2. 黄金链路

新增独立 full-stack Playwright 流程，不使用 `page.route` 或 `routeWebSocket` Mock：

1. Alembic 从空测试库迁移；
2. 用依赖注入启动真实 FastAPI，测试专用确定性模型返回合法六协议；
3. 浏览器创建 Run、邀请 NPC、发送玩家消息；
4. 请求真实经过 FastAPI、RunService、LangGraph 和 Repository；
5. NPC 回复通过真实 WebSocket/REST 出现在 React 页面；
6. 刷新页面后从 PostgreSQL 恢复；
7. 验证 `afterSeq` 补发和 Day-end；
8. 检查数据库消息与公开 UI 一致且私有字段未出现。

测试模型只能通过显式依赖注入进入 test app，生产默认路径仍使用 ArkClient；不得增加可被生产环境误开的“假模型环境变量后门”。

## 8. 工作流 D：仓库与 README 最终交付

### D1. 仓库整理

- 对当前 untracked probe/partial/dry-run 建立白名单，canonical 留在 `project/evaluation-results/`；
- 原始实验移动到本地 ignored archive，或增加精确 `.gitignore` 规则；不使用覆盖式删除；
- canonical 报告只保留最小机器可读 JSON、可读 Markdown、SHA-256 和生成命令；
- 报告不得包含绝对本机路径；
- 最终 `git status` 只允许本阶段计划文件，提交后必须干净；
- 运行 `git diff --check` 和密钥/凭据扫描。

### D2. README 信息架构

README 第一屏应包含：

- 一句话项目定位和一张真实界面截图；
- Mermaid 架构图：React/Phaser -> REST/WebSocket -> FastAPI/RunService -> LangGraph Agents -> PostgreSQL/pgvector -> Ark；
- 核心技术亮点：权威世界状态、owner-safe Memory、混合检索、可复现评测、真实成功/失败对照；
- 当前可信指标：测试数、47 Case、Schema/hard failure、检索指标、预注册成功率、P95、Token/成本；
- Judge advisory、固定策略只证明可达性等限制；
- 5 分钟本地运行和一条全量验证命令；
- canonical 报告、架构文档和演示入口链接。

生成 2—3 张 Playwright 实机截图和一段 60—180 秒本地演示视频或录制说明。资产只展示公开 UI，不包含密钥、私有 Prompt、Memory 或数据库凭据。

## 9. 总门禁

完成前至少执行：

```powershell
E:\anaconda3\envs\qinghuai-chat\python.exe -m pytest -c core/backend/pyproject.toml -q
cd core/backend
E:\anaconda3\envs\qinghuai-chat\python.exe -m ruff check app scripts migrations ../../test/backend
E:\anaconda3\envs\qinghuai-chat\python.exe -m mypy
cd ../frontend
pnpm lint
pnpm typecheck
pnpm test
pnpm build
pnpm test:e2e
```

另需在专用 PostgreSQL 测试库运行全部数据库测试，在 CI 运行新的 full-stack E2E，并确认：

- 0 个本阶段关键 PostgreSQL测试 skip；
- 0 个真实网络调用发生在普通测试/CI；
- 47 Case 最终完整复评有单次 canonical 报告；
- 预注册 manifest 与所有 planned run 一一对应；
- README 数字与 canonical 报告一致；
- 工作树整理完成并形成上述可审计提交。

## 10. 最终交付物

- 本方案与最终 Goal 提示词；
- 语义最终 before/after、检索报告、人工标注包、Judge 校准报告；
- 预注册 manifest、15 局全分母报告和可达性汇总；
- CI workflow、确定性 test app、真实 full-stack Playwright 测试；
- 更新后的 README、架构图、截图、演示材料；
- `project/FINAL_INTERVIEW_READINESS_ACCEPTANCE.md`；
- 可审计 Git 提交记录和干净工作树。
