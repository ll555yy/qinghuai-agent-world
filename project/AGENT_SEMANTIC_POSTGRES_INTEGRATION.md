# Agent 语义评测 PostgreSQL 检索集成

## 范围

`test/backend/integration/test_semantic_evaluation_postgres.py` 在专用
`QINGHUAI_TEST_DATABASE_URL` 上直接运行真实的
`DatabaseMemoryRetriever -> RuleScorer` 链路。测试数据只写入测试 Run，并在
`finally` 中按 Run ID 删除，不读取或回退到开发库、生产库或工作区 `.env`。

覆盖范围包括：

- `run_id + owner_npc_id` 过滤；
- 关键词检索；
- Actor、Goal、Topic alias 查询；
- 2048 维确定性测试向量写入真实 pgvector，并执行 vector 检索；
- 1-hop 与 2-hop Graph 扩展；
- 具有相似向量的其他 owner Memory 不进入候选；
- 跨 owner Graph 邻居不进入扩展结果或最终结果；
- 每种结果通过 `RuleScorer` 检查 owner boundary、Precision@K、Recall@K、
  candidate/system/end-to-end 安全字段。

测试 embedding 是确定性的本地 adapter，只用于让 PostgreSQL 的 pgvector
路径可重复，不代表线上 Embedding 质量。fixture 或 Fake Embedding 指标与
真实 PostgreSQL 指标必须分开报告。

## 运行方式

从仓库根目录运行，必须显式提供专用测试库 URL：

```powershell
$env:QINGHUAI_TEST_DATABASE_URL = "postgresql+psycopg://<test-user>:<test-password>@<test-host>:5432/<dedicated-test-db>"
python -m pytest -c core/backend/pyproject.toml -q test/backend/integration/test_semantic_evaluation_postgres.py
python -m ruff check test/backend/integration/test_semantic_evaluation_postgres.py
```

普通离线环境不设置该变量时，EvaluationRunner 的 skip 保护测试会通过，真实
PostgreSQL 测试会收集后 skip；它不会读取 `.env` 中的 `DATABASE_URL`，也不会
自动连接开发库或生产库。

## 当前执行状态

2026-08-23 已在本轮创建的独立临时 pgvector 17 容器中执行。该容器使用独立
端口 `55432`、独立数据库和测试账号，不是开发库；Alembic 已升级到 head。

```text
2 passed, 1 warning in 1.21s
```

两项分别证明无数据库授权时的 skip 保护，以及上述完整
`DatabaseMemoryRetriever -> RuleScorer` PostgreSQL 链路通过。测试数据在
`finally` 中按唯一 Run ID 删除。验收结束后，本轮创建且带 `--rm` 的临时
容器 `qinghuai-semantic-eval-test-20260823` 已停止并自动删除；现有开发容器
未被触碰。

这个结果只证明专用测试数据上的 PostgreSQL 查询路径和 owner 隔离，不代表真实
线上 Embedding 的语义质量。历史 fixture Precision/Recall/MRR 仍不得冒充数据库
指标；真实 Candidate/Judge/Embedding 复评继续需要用户单独授权。
