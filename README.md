# 青槐老巷聊天世界

当前仓库按职责分为三个主要目录：

- `core/`：前后端代码、运行时场景配置和生成类型。
- `test/`：所有测试、模拟和测试数据。
- `project/`：已确认的设计文档与一致性审计。

当前已完成无需前端即可运行的七天后端闭环：世界时间与事件、NPC 错峰行动、移动和邀请、最多三人聊天、玩家加入与自由发言、私有 Memory Graph 召回、离场沉淀、D-065 自动收束、长聊天滚动摘要以及 Day7 固定结算。火山方舟文本模型与 Embedding 的代码路径已经接入；真实模型质量仍须用本机密钥完成六协议和三局七日验收，未验收前不宣称后端可玩性完成。

权威状态可选择进程内存或 Docker PostgreSQL + pgvector。数据库模式会持久化 Run、消息、Goal、关系、Memory Graph 和章节状态，后端重启后可以继续运行；React/Phaser 前端尚未开发。权威玩法见 `project/PROJECT_DESIGN.md`，数据库阶段设计与验收见 `project/DATABASE_BACKEND_DESIGN.md` 和 `project/DATABASE_BACKEND_IMPLEMENTATION_REPORT.md`。

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
python -m uvicorn core.backend.app.main:app --reload
```

只做快速单元测试时不设置上述变量，后端会显式使用内存仓储。数据库模式连接失败或未迁移会直接报错，不会悄悄退回内存。

## 本地验证

在 Conda 环境 `qinghuai-chat` 中，从仓库根目录运行：

```powershell
python -m pytest -q
python -m ruff check --config core/backend/pyproject.toml core/backend/app test/backend
python -m mypy --config-file core/backend/pyproject.toml core/backend/app
```

需要启动 API 时：

```powershell
python -m uvicorn core.backend.app.main:app --reload
```

需要真实模型时，复制 `.env.example` 到本机 `.env`，填写已经轮换的新 `ARK_API_KEY`。应用和验收脚本会自动读取仓库根目录的 `.env`；不要把它加入 Git。

先验证六类文本协议（默认命令只做 dry-run）：

```powershell
python core/backend/scripts/check_ark_connection.py
python core/backend/scripts/check_ark_connection.py --live
```

再在 `.env` 中填写一个实际返回 1024 维的 `ARK_EMBEDDING_MODEL` 或推理接入点 ID，先用固定公开文本探测真实维度：

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

真实七日模拟必须使用 PostgreSQL。默认依次运行旁观、支持林慧兰和支持赵磊三条路线，每局最多 600 次模型适配器调用、总计最多 1800 次；报告只保存指标和结构化结果：

```powershell
python core/backend/scripts/run_seven_day_simulation.py --real --backend postgres --route all --runs 1 --output simulation_reports
```

加 `--keep-runs` 才保留模拟 Run；否则在完成 Repository 重启恢复验证后清理这些模拟数据。真实报告目录不要提交，完成三局后按 `project/REAL_AI_EMBEDDING_SIMULATION_ACCEPTANCE.md` 记录结果和调参证据。

若确实要清空本机开发数据库（会删除全部本地 Run，无法恢复）：

```powershell
docker compose down -v
docker compose up -d database
cd core/backend
alembic upgrade head
```
