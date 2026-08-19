# 青槐老巷聊天世界

当前仓库按职责分为三个主要目录：

- `core/`：前后端代码、运行时场景配置和生成类型。
- `test/`：所有测试、模拟和测试数据。
- `project/`：已确认的设计文档与一致性审计。

当前已完成无需前端即可运行的七天后端可玩闭环：世界时间与事件、NPC 错峰行动、移动和邀请、最多三人聊天、玩家加入与自由发言、私有 Memory Graph 召回、离场沉淀、D-065 自动收束、长聊天滚动摘要以及 Day7 固定结算。真实模型通过火山方舟 `doubao-seed-2.0-lite` 接入；未设置密钥时使用不编造剧情的安全结果。

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

需要真实模型时，复制 `.env.example` 到本机 `.env`，填写已经轮换的新 `ARK_API_KEY`，并使用：

```powershell
python -m uvicorn core.backend.app.main:app --reload --env-file .env
```

不要把 `.env` 加入 Git。

若确实要清空本机开发数据库（会删除全部本地 Run，无法恢复）：

```powershell
docker compose down -v
docker compose up -d database
cd core/backend
alembic upgrade head
```
