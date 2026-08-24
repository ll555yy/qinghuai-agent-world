# 青槐老巷聊天世界

一个由五名自治 NPC 持续推进的七日互动叙事：玩家可以旁观，也可以通过公开对话协调立场；世界状态、私有记忆和最终结局始终由后端权威规则裁决。

![青槐老巷书店世界界面](project/visual-qa/world-1440x900.png)

## 当前可信证据

| 能力 | Canonical 证据 | 当前结论 |
|---|---|---|
| Agent 语义 | 2026-08-23 单批 47 Case、81 个 observation | 47/47 完成，hard failure `0`，最终/首轮 Schema `100%`，29 个直接通过、18 个进入人工复核 |
| 直接问题 | 同一 canonical 复评 | Rule 通过率 `100%`；P95 `10,371.531 ms` |
| Memory 安全 | 14 次真实 PostgreSQL + pgvector tuning/holdout | holdout Precision@returned / Recall@K / MRR 均 `1.0`，FPR `0`，owner 越界与重复均 `0`，空查询 `1/1` |
| 七日可达性 | 历史三路线、9 个新格式真实样本 | observer、成功联盟、低投入失败均可达；这是固定策略可达性，不是自然玩家成功率 |
| 最终七日门禁 | 三轮预注册真实批次 | 均因 Ark Provider 连续超时而不完整；最新 v3 为 `planned=15 / attempted=11 / infraValid=10`，未通过最终门禁 |
| 前后端 | FastAPI + React/Phaser | 23/23 Vitest、11/11 浏览器契约、1/1 无 REST/WS Mock 的 PostgreSQL 全栈黄金链路通过 |

Judge 校准已真实完成 13/13，但 critical boolean macro accuracy 为 `79.4872%`、Injection 为 `2/3`，未过预注册阈值，因此 Judge 仍是 advisory，不能覆盖规则结果。三路线各 5 个连续 seed 的最终七日门禁尚未通过：v1、v2、v3 都保留完整分母，不替换失败 seed；最新 v3 保住 5 个 observer 和 5 个 pro_lin 检查点，但 Provider 故障阻断全部 pro_zhao。v3 还发现旧 pro_lin 策略只有 `1/5` 达到玩家任务 completed，因此新增 `strategy.pro_lin.v2`，并已在 `aa71928` 预注册连续 seed `20260855..20260859` 的 v4 holdout；Provider 稳定前不启动。当前项目可用于展示架构、语义整改、检索与 CI/E2E，但不能声称“最终七日统计门禁已完成”。最终数字以 [最终面试验收](project/FINAL_INTERVIEW_READINESS_ACCEPTANCE.md) 和其链接的 canonical JSON 为准。

## 架构

```mermaid
flowchart LR
    UI[React UI] --> Phaser[Phaser 书店场景]
    UI -->|REST / WebSocket| API[FastAPI]
    Phaser -->|公开权威投影| API
    API --> Service[RunService / WorldEngine]
    Service --> Graph[LangGraph NPC Agents]
    Graph --> Memory[Owner-safe hybrid Memory]
    Service --> DB[(PostgreSQL + pgvector)]
    Memory --> DB
    Graph -->|六类结构化协议| Ark[Volcengine Ark]
```

- FastAPI/RunService 是世界时间、参与者、Goal、关系、立场和结局的唯一权威写入入口。
- LangGraph Agent 负责行动、邀请、聊天、台词、摘要和离场沉淀，但不能绕过 ID、时间、证据和 owner 校验。
- Memory 使用关键词、Actor、Goal、Topic、向量和最多两跳 Graph 的混合检索；每次查询重新应用 `run_id + owner_npc_id` 边界。
- React + Phaser 只消费公开快照和按 `eventSeq` 排序的事件，不渲染私有 Goal、Memory 或内部 Prompt。

## 五分钟启动

需要 Docker、Python/Conda 和 pnpm。复制 `.env.example` 为本机 `.env`，只填写本地数据库密码；没有真实模型 Key 时仍可运行离线测试和无网络全栈 E2E。

```powershell
# 1. PostgreSQL + pgvector
docker compose up -d database

# 2. 从空库迁移并启动后端
$env:DATABASE_URL="postgresql+psycopg://qinghuai:你的本地密码@127.0.0.1:5432/qinghuai"
cd core/backend
alembic upgrade head
cd ../..
python -m core.backend.app

# 3. 另开终端启动前端
cd core/frontend
pnpm install --frozen-lockfile
pnpm dev
```

浏览器打开 `http://127.0.0.1:5173`。Vite 将 `/api` 和 `/ws` 转发到 `http://127.0.0.1:8000`。

## 全量验证

普通测试和 CI 不设置 `ARK_API_KEY`，不会访问真实方舟。

```powershell
# 后端离线门禁
python -m pytest -c core/backend/pyproject.toml -q
cd core/backend
python -m ruff check app scripts migrations ../../test/backend
python -m mypy
cd ../..

# 前端门禁
cd core/frontend
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test
pnpm build
pnpm test:e2e
```

PostgreSQL 关键测试必须使用专用测试库；不要把它指向开发库或生产库：

```powershell
$env:QINGHUAI_TEST_DATABASE_URL="postgresql+psycopg://<test-user>:<password>@127.0.0.1:<test-port>/<test-db>"
python -m pytest -c core/backend/pyproject.toml -q test/backend/integration
```

## 证据与演示

- [最终面试验收](project/FINAL_INTERVIEW_READINESS_ACCEPTANCE.md)（记录已通过项与当前阻塞项）
- [47 Case 真实复评](project/evaluation-results/live-final-canonical-2026-08-23/agent_semantic_evaluation.md)
- [PostgreSQL 检索留出集](project/evaluation-results/postgres-retrieval-final-2026-08-23/postgres_retrieval_benchmark.md)
- [Agent 语义整改验收](project/AGENT_SEMANTIC_REMEDIATION_ACCEPTANCE.md)
- [Agent Before / After](project/AGENT_SEMANTIC_REMEDIATION_BEFORE_AFTER.md)
- [七日真实模拟结果](project/REAL_SEVEN_DAY_SIMULATION_RESULTS.md)
- [七日最终批次 v1 中断证据](project/simulation-results/final-preregistered-v1-interrupted-2026-08-23/seven_day_gameplay_evidence.md)
- [七日恢复批次 v2 中断证据](project/simulation-results/final-preregistered-recovery-v2-interrupted-2026-08-24/seven_day_gameplay_evidence.md)
- [七日恢复批次 v3 中断证据](project/simulation-results/final-preregistered-recovery-v3-interrupted-2026-08-24/seven_day_gameplay_evidence.md)
- [七日模拟说明](project/SEVEN_DAY_SIMULATION_GUIDE.md)
- [前端验收报告](project/FRONTEND_ACCEPTANCE_REPORT.md)
- [演示录制说明](project/DEMO_RECORDING_GUIDE.md)（最终阶段完成后生成）

公开 UI 截图：

![1280×720 世界界面](project/visual-qa/world-1280x720.png)

![聊天申请面板](project/visual-qa/world-980x720-panel.png)

## 证据边界

- 固定路线只能证明成功、失败和旁观路径可达，不能证明普通玩家自然发现成功路径的概率。
- 在两名真实人工完成相互独立标注与仲裁前，LLM Judge 只提供 advisory 信号，不能成为自动发布门。
- fixture 中预置的 `retrievedMemoryIds` 只验证评测口径；线上检索质量必须由真正调用 `DatabaseMemoryRetriever.search()` 的 PostgreSQL 留出集证明。
- 仓库不保存真实 Key、数据库密码、完整私有 Prompt、coreSecrets、生产私有 Memory、原始用户聊天或未脱敏 Trace。

如需显式运行真实 Ark 验收，请先阅读对应报告和脚本的 dry-run 输出。只有带 `--live` 或 `--real` 的命令才允许产生网络调用和费用。
