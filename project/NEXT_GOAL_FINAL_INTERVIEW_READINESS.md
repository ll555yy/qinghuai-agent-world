# 下一阶段 Goal：完成最终面试交付四项门禁

以下正文供用户复制后自行设置 Goal；本文件不代表已经创建或启动 Goal。

```text
继续开发 C:\Users\yangruiqi\Desktop\chat 中的“青槐老巷聊天世界”。

目标：完成项目面试交付前的最后四项工作：（1）关闭 Agent 语义评测暴露的确定性、检索和校准问题；（2）用运行前提交的 manifest 和全分母连续 seed 重新证明成功/失败/旁观路线；（3）建立无真实网络的 CI 与 PostgreSQL + FastAPI + LangGraph + React/Phaser 真实全栈 E2E；（4）整理仓库、canonical 证据和 README，并在完成后统一验收、形成可审计 Git 提交。不要增加无关玩法或框架。

开始前必须完整阅读：
- project/PROJECT_RULES.md
- project/FINAL_INTERVIEW_READINESS_PLAN.md
- project/AGENT_SEMANTIC_EVALUATION_DESIGN.md
- project/AGENT_SEMANTIC_EVALUATION_ACCEPTANCE.md
- project/AGENT_SEMANTIC_REMEDIATION_ACCEPTANCE.md
- project/AGENT_SEMANTIC_REMEDIATION_BEFORE_AFTER.md
- project/REAL_SEVEN_DAY_SIMULATION_RESULTS.md
- project/SEVEN_DAY_SIMULATION_GUIDE.md
- project/FRONTEND_ACCEPTANCE_REPORT.md
- core/backend/app/evaluation/ 全部实现
- core/backend/app/simulation/ 全部实现
- core/evaluation/ 版本化 Case 与 fixtures

先检查 Git 工作树并保护用户已有修改。当前 HEAD 已包含语义评测整改；工作区仍有未提交的七日可达性 runner、证据汇总器、脚本、测试、结果文档，以及大量未跟踪 probe/partial/dry-run 报告。不得覆盖、回滚、删除或顺手格式化这些文件。先生成文件归属清单、canonical 白名单和本地归档方案，再修改代码。

协作方式：可以按任务需要使用至多三个 Luna Max（gpt-5.6-luna，reasoning=max）子智能体并行实施，最多四个并发槽位包含主会话。文件边界清晰且子任务独立时优先并行；存在强依赖或任务很小时由主会话直接执行，不为形式上的并行强拆任务。
- Luna A 独占 evaluation、core/evaluation 和 evaluation tests，负责检索精度、人工标注包、Judge 校准和最终 47 Case 复评准备。
- Luna B 独占 simulation、七日脚本/测试和 experiment manifest，负责预注册、全分母汇总和路线证据。
- Luna C 独占 CI、测试专用 full-stack app 和 Playwright full-stack E2E，负责无真实网络黄金链路。
- 主会话负责 README、canonical 报告、预算、真实调用、跨范围接口、全量门禁和 Git。
子智能体不得 commit/reset/rebase/push，不得修改不属于自己的文件；需要跨范围接口时先报告主会话。主会话必须独立复核所有子智能体结果。

真实模型授权：用户已经明确授权本 Goal 按任务需要调用真实 Candidate、Judge 和 Embedding，无需每次再次申请，也不要因常规真实调用停下来等待授权。批量调用前先用少量 dry-run 验证配置和计费口径，随后直接执行；全程记录调用次数、Token、单价、费用、失败和重试，不设置未经用户要求的任意费用硬上限。真实调用由主会话统一编排，避免多个子智能体重复或失控消费；出现异常重复消费或 Provider 故障时先停止异常批次并修复。缺少 Key 或 Provider 不可用时如实记录，不得伪造真实结果。

阶段 0：冻结当前成果和提交边界
1. 记录 HEAD、git status、tracked diff、untracked 顶层目录和 canonical SHA-256。
2. 将文件分类为：现有七日成果、canonical 评测、临时 probe/partial、最终阶段新增。
3. 保留有调查价值的原始实验；把临时结果移入本地 ignored archive 或增加精确 ignore，不做覆盖式删除。
4. 先整理当前七日成功路线实现、证据汇总器、测试和历史结果，运行相关离线门禁后形成 `feat: finalize current reachability evidence baseline` 提交。

阶段 1：提交预注册实验，不得先跑后登记
1. 新增版本化 experiment manifest，包含 experimentId、创建时间、Git commit、模型、Prompt/策略/场景/Case SHA-256、每条路线连续 seed、计划次数、预算、超时、重试和预先定义的 infra-invalid 规则。
2. 每条路线固定 5 个连续 seed，共 15 局；历史报告不计入这个新批次的成功率。
3. manifest 和校验测试必须先形成 `test: preregister final agent validation matrix` Git 提交，提交完成后才允许运行真实模型。
4. 后续不得替换失败 seed，也不得只向汇总器传成功文件。

阶段 2A：完成语义评测闭环
1. 诊断 Memory Precision@K=0.474359 的来源。先确认它包含固定 K 分母和 fixture 预置 `retrievedMemoryIds`，不能直接冒充线上 PostgreSQL 排序质量。
2. 保留历史 `strictPrecisionAtK`，新增 `precisionAtReturned`、false-positive rate、空查询正确率、重复结果数和 `retrievalSource=fixture|postgres|live_embedding`；三种来源不得混算或通过改分母覆盖历史值。
3. 建立真正调用 `DatabaseMemoryRetriever.search()` 的 PostgreSQL benchmark，分别统计 vector、关键词、Actor、Goal、Topic、Graph 各阶段指标；不能用 Case 预置 ID 冒充数据库检索。
4. 将数据拆为调参集和留出集；优先尝试可解释阈值、有限 Graph seed、分数归一化或 RRF。不为凑满 K 返回弱相关 Memory，没有证据时不增加模型 reranker。
5. 在专用 PostgreSQL 留出集验证：precisionAtReturned >=0.90、Recall@K >=0.90、false-positive rate 达预注册门槛、MRR 不低于当前基线、空查询/去重通过、owner 越界=0。若仍要求 strictPrecisionAtK>=0.75，必须先固定“是否凑满 K”的产品语义。
6. 冻结 20—30 个代表性人工标注样本，输出两份相互独立的人工标注表、说明和仲裁表。子智能体和 Judge 不能冒充人工；没有两名真实人工结果时 Judge 保持 advisory，不伪称完成校准。
7. calibration expected schema 区分 requiredMajorIssues/forbiddenMajorIssues；保留历史 exact match 诊断。Judge 自动门要求 13/13 完成、critical boolean macro accuracy>=80%、score-band>=80%、Injection=3/3、provider/schema error=0；未全部满足则继续 advisory。
8. 合入最终 scope 修复后，完整运行单次 47 Case live 复评，不得拼接局部恢复。保留 before/after、Bad Case、人工队列、P95、Token、费用和 SHA-256。
9. Candidate 目标：first-attempt Schema >=90%、最终 Schema >=95%、hard failure=0、direct-question=100%、Memory 单次调用=100%。未达到的指标如实保留，不能删 Case 或放宽安全门。

阶段 2B：完成预注册七日全分母证据
1. manifest 额外固定 strategyId/kind/version/SHA-256、privateInputsUsed=false，并为 canonical JSON 生成外部 SHA-256。
2. runner 为每个 manifest seed 生成唯一 attemptId；汇总器从 manifest 枚举计划运行，而不是从调用者传入的 JSON 推断分母。
3. 每个 attempt 调用前先落 `started`，最终只能是 completed/provider_failed/timeout/budget_exhausted/runner_failed/not_started。创建 Run 前失败也必须留下 terminal record。
4. 报告 planned、attempted、infraValid、gameplayPass、coverage rate、ITT success rate 和 valid-run success rate。
5. 缺失、非 terminal、超时、预算耗尽、Provider 失败和 gameplay 失败都必须出现；未计划 seed、重复 attempt、digest 不一致直接令 complete=false。
6. canonical 报告只保存相对路径/source ID，不得保存 `C:\Users\...` 绝对路径。
7. `observer` 玩家发言必须为0；`pro_lin` 至少4/5 planned seed成功且至少2局玩家任务completed；`pro_zhao` 至少4/5 planned seed保持失败对照。
8. 所有局记录 Token、估算成本、改变立场人数、Goal 完成率、Repository 恢复、临时 Run 清理和完整七日事件。
9. 固定路线只证明可达性。另输出真实玩家试玩协议和匿名记录模板；没有真实玩家时不伪造结果，也不让自动化代理充当玩家样本。

阶段 2C：建立 CI 和真实 full-stack E2E
1. 新增 GitHub Actions 或等价 CI，使用固定 pgvector PostgreSQL 服务。
2. 后端 job 运行 pytest、Ruff、mypy、应用导入和密钥扫描；数据库 job 从空库运行 Alembic upgrade/check，并执行 persistence 与 semantic PostgreSQL 测试，关键测试不得 skip。
3. 前端 job 使用 frozen pnpm lock，运行 lint、typecheck、Vitest 和 production build。
4. 增加不 Mock REST/WebSocket 的 full-stack Playwright 黄金链路：真实 PostgreSQL、真实迁移、真实 FastAPI/RunService/LangGraph/Repository、确定性 Fake TextModel/Embedding、真实 React/Phaser。
5. 覆盖创建 Run、邀请、玩家发言、NPC 经 Agent Graph 回复、WebSocket、刷新恢复、afterSeq 和日终；验证数据库消息与 UI 一致且私有字段不出现。
6. Fake 端口只能通过测试 app 显式依赖注入，生产默认仍使用 ArkClient；不得增加可在生产误开的假模型环境变量。
7. CI 永远不设置真实 ARK_API_KEY，不访问真实方舟。失败时上传脱敏 Playwright/pytest artifacts。

阶段 2D：整理仓库和 README
1. `project/evaluation-results/` 只保留 canonical baseline/after/offline；probe、partial、dry-run 和重复实验进入 ignored 本地归档，不提交。
2. canonical 报告保留最小 JSON、Markdown、SHA-256、生成命令和相对 source ID；运行密钥、凭据和绝对路径扫描。
3. README 第一屏加入一句话定位、真实界面截图和当前可信指标。
4. 增加 Mermaid 架构图：React/Phaser -> REST/WebSocket -> FastAPI/RunService -> LangGraph Agents -> PostgreSQL/pgvector -> Ark。
5. README 写明 47 Case、Schema/hard failure、检索指标、预注册成功率、P95、Token/费用和 Judge advisory 限制；数字必须来自 canonical 报告。
6. 增加 5 分钟启动、全量验证命令、canonical 证据链接、2—3 张公开 UI 截图和 60—180 秒演示视频或可复现录制说明。

总门禁：
- 全量 backend pytest、Ruff、mypy、应用导入通过；
- 专用 PostgreSQL 下所有 persistence/semantic 关键测试通过且不 skip；
- frontend lint、typecheck、Vitest、build、现有 Playwright 和新 full-stack Playwright 通过；
- CI 在无真实 Key 环境通过；
- 47 Case 最终复评是一次完整 canonical run；
- manifest 中 15 个 planned run 全部能在汇总中找到，不允许选择性遗漏；
- README 指标与 canonical JSON 一致；
- `git diff --check`、凭据扫描通过；
- 工作树最终干净。

最终交付：
- project/FINAL_INTERVIEW_READINESS_ACCEPTANCE.md
- 语义最终 before/after、检索留出集报告、人工标注包、Judge 校准结果
- 预注册 manifest、15 局全分母报告和汇总
- CI workflow、测试专用依赖注入 app、真实 full-stack E2E
- 更新后的 README、架构图、截图和演示材料
- 实际费用、测试数、未达门槛和 Judge 状态
- 最终 `ci: complete final interview readiness gate` 提交

禁止事项：不新增玩法、人设、Goal、结局阈值、新 Agent 框架、MCP/A2A、Neo4j、Redis、Kubernetes、SFT/RL、多模型复核；不提交真实密钥、数据库密码、完整私有 Prompt、coreSecrets、生产私有 Memory、原始用户聊天或未脱敏 Trace；不 push 远端。

执行完成后，主会话必须独立检查子智能体的代码和报告，运行所有门禁，修正跨范围问题，写最终验收报告并整理提交。最终回复必须报告：三个提交哈希、真实调用次数/Token/估算费用、预注册 ITT 成功率、检索 Precision/Recall/MRR、47 Case 指标、Judge 是否仍 advisory、CI 与全栈 E2E 结果、仍无法由自动化完成的人工标注/试玩边界。不要替用户创建新的后续 Goal。
```
