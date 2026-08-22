# 青槐老巷聊天世界

当前仓库按职责分为三个主要目录：

- `core/`：前后端代码、运行时场景配置和生成类型。
- `test/`：所有测试、模拟和测试数据。
- `project/`：已确认的设计文档与一致性审计。

当前已完成无需前端即可运行的七天后端闭环：世界时间与事件、NPC 错峰行动、移动和邀请、最多三人聊天、玩家加入与自由发言、私有 Memory Graph 召回、离场沉淀、D-065 自动收束、长聊天滚动摘要以及 Day7 固定结算。火山方舟六类文本协议、2048 维 Embedding、真实 Day1 闭环以及 observer / pro_lin / pro_zhao 三条真实七日路线均已通过验收；后端前端门禁已解除。

权威状态可选择进程内存或 Docker PostgreSQL + pgvector。数据库模式会持久化 Run、消息、Goal、关系、Memory Graph 和章节状态，后端重启后可以继续运行。React + Phaser 前端已实现开局、任务选择、二维书店场景、邀请/加入/聊天、日终、断线恢复和 Day7 结局。权威玩法见 `project/PROJECT_DESIGN.md`，前端设计与验收见 `project/FRONTEND_DESIGN.md` 和 `project/FRONTEND_ACCEPTANCE_REPORT.md`。

## 启动前端

先启动数据库和后端，再在另一个终端运行：

```powershell
cd core/frontend
pnpm install
pnpm dev
```

浏览器访问 `http://127.0.0.1:5173`。Vite 会把 `/api` 和 `/ws` 转发到 `http://127.0.0.1:8000`。首次运行浏览器验收前执行 `pnpm exec playwright install chromium`。

## 启动 PostgreSQL

复制 `.env.example` 为 `.env`，把 `POSTGRES_PASSWORD` 和 `DATABASE_URL` 中的密码改成同一个本地密码，然后运行：

```powershell
docker compose up -d database
cd core/backend
$env:DATABASE_URL="postgresql+psycopg://qinghuai:你的本地密码@127.0.0.1:5432/qinghuai"
alembic upgrade head
cd ../..
```

数据库模式启动 API：

```powershell
$env:QINGHUAI_PERSISTENCE_BACKEND="postgres"
$env:DATABASE_URL="postgresql+psycopg://qinghuai:你的本地密码@127.0.0.1:5432/qinghuai"
python -m core.backend.app
```

只做快速单元测试时不设置上述变量，后端会显式使用内存仓储。数据库模式连接失败或未迁移会直接报错，不会悄悄退回内存。

## 本地验证

在 Conda 环境 `qinghuai-chat` 中，从仓库根目录运行：

```powershell
python -m pytest -c core/backend/pyproject.toml -q
cd core/backend
python -m ruff check app scripts migrations ../../test/backend
python -m mypy
cd ../..
```

要包含 PostgreSQL 集成测试，先把 `QINGHUAI_TEST_DATABASE_URL` 指向专用测试库；不要让测试连接开发库或生产库。

需要启动 API 时：

```powershell
python -m core.backend.app
```

需要真实模型时，复制 `.env.example` 到本机 `.env`，填写已经轮换的新 `ARK_API_KEY`。应用和验收脚本会自动读取仓库根目录的 `.env`；不要把它加入 Git。

先验证六类文本协议（默认命令只做 dry-run）：

```powershell
python core/backend/scripts/check_ark_connection.py
python core/backend/scripts/check_ark_connection.py --live
```

Embedding 已选择 Agent Plan 的 `doubao-embedding-vision`。真实探测返回 2048 维，配置示例已包含对应 Base URL；仍可用固定公开文本复验当前账号：

```powershell
python core/backend/scripts/check_ark_embedding.py
python core/backend/scripts/check_ark_embedding.py --live
```

探测通过后，按指定 Run 幂等回填 Memory；不写 `--live` 不会产生调用：

```powershell
cd core/backend
python scripts/backfill_embeddings.py --run-id run_xxx
python scripts/backfill_embeddings.py --live --run-id run_xxx --limit 100 --batch-size 32
cd ../..
```

在长模拟前运行一次真实小型聊天闭环（默认 dry-run）：

```powershell
python core/backend/scripts/run_real_chat_acceptance.py
python core/backend/scripts/run_real_chat_acceptance.py --live
```

真实七日模拟必须使用 PostgreSQL。默认依次运行旁观、支持林慧兰和支持赵磊三条路线，每局最多 600 次模型适配器调用、总计最多 1800 次；报告只保存指标和结构化结果：

```powershell
python core/backend/scripts/run_seven_day_simulation.py --real --backend postgres --route all --runs 1 --output simulation_reports
```

加 `--keep-runs` 才保留模拟 Run；否则在完成 Repository 重启恢复验证后清理这些模拟数据，并在安全报告中记录删除结果。真实报告目录不要提交；当前三路线结果见 `project/REAL_SEVEN_DAY_SIMULATION_RESULTS.md`，完整调参依据见 `project/PROMPT_GAMEPLAY_TUNING_LOG.md`。

若确实要清空本机开发数据库（会删除全部本地 Run，无法恢复）：

```powershell
docker compose down -v
docker compose up -d database
cd core/backend
alembic upgrade head
```
