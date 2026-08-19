# PostgreSQL + pgvector 后端阶段实施报告

- 日期：2026-08-19
- 状态：完成并通过验收
- 对应设计：`DATABASE_BACKEND_DESIGN.md`
- 对应验收：`DATABASE_BACKEND_ACCEPTANCE.md`

## 1. 已完成范围

- `compose.yaml` 只运行固定版本 `pgvector/pgvector:0.8.1-pg17-bookworm`，带持久卷和健康检查；FastAPI 继续在 Conda 环境本地运行。
- SQLAlchemy 2 Async、psycopg 3、Alembic、pgvector 依赖和 Windows Selector event loop 兼容已接入。
- 两个 Alembic revision 建立规范化 Run、会话、Message、Goal、关系、Memory、Graph 边、章节状态、事件、幂等命令和恢复辅助表；`vector(384)` 使用 embedding 非空的部分 HNSW 索引。
- PostgreSQL `RunRepository` 支持事务保存、存储 revision 乐观并发、持久事件补取、进程内聚合 identity cache 和重启反序列化；Lock 与在途模型计数不会进入数据库。
- 每个有效公共命令返回前保存；聊天模型等待前先形成检查点，数据库事务不会跨模型等待持有。
- 静态 YAML Actor、NPC 人设、Topic、Goal definition、Agenda 在应用启动时同步，表结构仍只由 Alembic 管理。
- 数据库模式连接失败或 schema 未迁移时启动失败，不回退内存；内存仓储保留给快速单元测试。
- Agent 的 `retrieve_owned_memories` 工具可绑定数据库检索器，执行 owner 固定的关键词、Actor、Goal、Topic、pgvector 和一至二跳 Graph 混合召回；每一跳再次校验 owner。
- 实现 D-065 纯 NPC 两轮无人发言关闭、所有重置条件和 18:00 边界。
- Segment 超过 20 条消息后生成滚动摘要，保留最近 8 条原文；摘要失败不推进游标，原始 Message 始终完整保存。
- WebSocket 支持 `?afterSeq=` 重连补发持久事件；默认连接行为仍先发送公开快照。

## 2. 实际数据库验证

Docker 开发库和独立 `qinghuai_test` 测试库均使用本机容器。已执行：

```powershell
docker compose up -d database
alembic upgrade head
alembic check
alembic downgrade base
alembic upgrade head
```

结果：

- 容器 `qinghuai-chat-database-1` 为 `healthy`，端口 `5432`。
- 空库能从 base 升级到 `34039a40f40d`。
- `34039a40f40d -> 0001_initial_schema -> base` 降级成功，再升级成功。
- `alembic check` 返回 `No new upgrade operations detected`。
- `vector` extension、规范化表、外键、范围 CHECK、普通索引和部分 HNSW 索引均由 migration 建立。

## 3. 自动验证覆盖

PostgreSQL 集成测试使用独立 `qinghuai_test` 数据库，覆盖：

- FastAPI 数据库模式启动和健康检查；
- 创建 Run、邀请、建立聊天、玩家消息、NPC 离场沉淀；
- 重建 FastAPI、Repository 和 RunService 后恢复同一 Run；
- 重启后继续推进世界时间和 eventSeq；
- commandId 重放不产生第二次变化，旧 storage revision 被拒绝；
- Message、Goal、关系、Memory 和章节状态规范化记录可查询；
- 固定 384 维 Fake Embedding 实际执行 pgvector 距离表达式；
- Actor、Goal、Topic、关键词种子与 Graph 邻居召回；
- 同 owner 邻居可进入结果，跨 owner 高相似 Memory 和跨 owner Graph 边被排除；
- 人为制造关系 CHECK 失败后，Goal 和章节立场等同事务修改全部回滚；
- Day7 共识、妥协、未提交三分支及结构化结局行在重启后保持一致。

最终质量门禁结果：`113 passed, 1 warning`；Ruff、mypy、Alembic check 和 Docker health 全部通过。唯一 warning 来自 FastAPI TestClient 间接依赖的 Starlette 弃用提示，不影响项目逻辑。测试不访问真实火山方舟或真实 Embedding 服务。

## 4. 第一版有意保留的简化

- 后端仍是单进程权威服务；没有 Redis、消息队列、Neo4j、分布式锁或多数据库同步。
- `run_state_items` 按 section/item 保存少量形状易变的恢复数据；Message、Goal、Memory、关系、Topic、Graph 边和章节状态同时具有独立规范化记录，不保存单块 Run JSON 或 pickle。
- 未配置真实 EmbeddingPort 时 embedding 保持 NULL，召回使用真实关键词与 Graph 路径；不会生成伪向量。
- 规范化运行时投影采用小规模全量替换，适合第一版每局 6 个角色和短章节。需要实测出现写入瓶颈后再改为增量 upsert。
- 前端仍不在本阶段范围内。
