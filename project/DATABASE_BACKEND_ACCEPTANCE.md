# PostgreSQL + pgvector 后端阶段验收标准

- 状态：已通过
- 日期：2026-08-19
- 依据：`PROJECT_DESIGN.md`、`PROJECT_RULES.md`、`SYSTEM_DESIGN.md`、`TECH_STACK.md`、`DATABASE_BACKEND_DESIGN.md`

## 1. 基础设施

- [x] `compose.yaml` 只包含 PostgreSQL/pgvector 服务，镜像版本固定。
- [x] `.env.example` 不含真实密钥；`.env`、数据库数据和本地凭据被 Git 忽略。
- [x] `docker compose up -d` 后健康检查通过。
- [x] 数据库存在 `vector` 扩展。
- [x] FastAPI 不在 Docker 容器中运行。

## 2. 迁移与依赖

- [x] SQLAlchemy 2 Async、psycopg 3、Alembic 和 pgvector 依赖同时写入 `pyproject.toml` 与 `environment.yml`。
- [x] Alembic 从空库 `upgrade head` 成功。
- [x] 至少一次 `downgrade base` 后再次 `upgrade head` 成功。
- [x] 应用不调用 `metadata.create_all()` 代替 migration。
- [x] pgvector 列、普通索引、外键、唯一约束、关系范围 CHECK 和向量索引实际存在。

## 3. 数据模型覆盖

- [x] Run、世界时间、序号、Actor 状态、每日思考、世界事件和 Run Event 可恢复。
- [x] Conversation、历史/当前参与者、Segment、Message、邀请和 JoinRequest 可恢复。
- [x] Goal、Relationship、Memory、Topic 和所有 Graph 边是独立可查询记录。
- [x] 会话草稿、Memory cache、Consolidation、章节立场、授权、Agenda 态度和结局可恢复。
- [x] 没有用 pickle 或单个 Run JSON 代替上述权威表。
- [x] asyncio Lock、Queue 和在途模型计数未序列化，恢复时重新建立。

## 4. Repository 与事务

- [x] InMemoryRunRepository 仍可显式用于纯单元测试。
- [x] PostgreSQL 模式配置缺失或连接失败时明确失败，不退回内存。
- [x] 每个改变状态的公共命令返回前状态已经提交。
- [x] 释放 Run lock 等待模型前，已接受的输入和原始 Message 已形成数据库检查点。
- [x] 数据库拒绝较新 `state_version` 被旧 Run 覆盖。
- [x] 离场时 Memory、Goal、Relationship、章节状态和 consolidation 状态在一个事务内提交或整体回滚。
- [x] 同一 commandId 和 consolidation 幂等键不会重复写入。
- [x] 事务提交失败时不发布虚假的成功 WebSocket 事件。

## 5. 重启与事件恢复

- [x] 使用数据库创建 Run 并完成至少一次邀请、聊天消息和离场沉淀。
- [x] 销毁并重建 Repository/RunService 后，同一 runId 能恢复相同世界时间、消息、Goal、关系、Memory、章节状态和 eventSeq。
- [x] 恢复后的 Run 可以继续接受合法命令并产生递增 ID/eventSeq。
- [x] `afterSeq` 可以读取重启前持久化的遗漏事件。
- [x] `/health` 报告应用和数据库状态且不泄露连接信息。

## 6. Memory Graph 与向量

- [x] 模型 Schema 不能传入 owner；owner 由绑定 Agent/运行时注入。
- [x] 每个数据库查询在候选生成前限制 `run_id + owner_npc_id`。
- [x] 高度相似但属于另一 NPC 的 Memory 永远不能被召回或通过 Graph 扩展进入结果。
- [x] Actor、Goal、Topic、关键词候选和一至二跳 Memory 边扩展有测试。
- [x] 使用固定 Fake Embedding 的 PostgreSQL 集成测试实际执行 pgvector 距离查询和向量索引路径。
- [x] 未配置 Embedding 时只使用关键词 + Graph，不写入伪向量。
- [x] 排序稳定且返回不超过查询 limit/系统上限 8。

## 7. D-065 与聊天压缩

- [x] 纯 NPC Conversation 第一次完整无人发言调度只进入一次 idle 状态。
- [x] 第二次仍无人发言时以 `conversation_idle` 关闭。
- [x] 关闭走正常 Segment 摘要、每名 NPC ExitConsolidation 和状态清理。
- [x] 新消息、有效发言和参与者变化重置空闲计数。
- [x] 含玩家 Conversation 不会因为 NPC 连续 wait 自动关闭。
- [x] 18:00 仍能强制关闭两类 Conversation。
- [x] 超过压缩阈值后提示词包含共享滚动摘要和最近原文，不包含被摘要的重复原文。
- [x] 消息数未超限但本地估算 Token 超限时也能提前滚动压缩。
- [x] 参与者变化后，继续参与者获得上一 Segment 最近 4 条边界原文。
- [x] 后来加入 NPC 不能看到加入前滚动摘要。
- [x] 后来加入 NPC 不能看到加入前边界原文。
- [x] 数据库仍保存全部原始 Message。
- [x] SegmentSummary 失败时原文和摘要游标不丢失，聊天不被阻塞。

## 8. 玩法与隐私回归

- [x] D-057 `departed` 行为继续成立。
- [x] D-058 全体现有参与者一致同意加入继续成立。
- [x] NPC/玩家加入前历史可见性差异继续成立。
- [x] 两场上限、三人上限和角色唯一 Conversation 继续成立。
- [x] Day1～Day7 世界事件、17:00 截止和 18:00 收尾继续成立。
- [x] Day7 三种章节结果和五项 Agenda 采纳矩阵在数据库模式下通过。
- [x] REST/WebSocket 不公开秘密、Goal、关系数值、私有 Memory、Prompt、Agent trace 或数据库内部字段。

## 9. 质量门禁

- [x] 单元测试不要求 Docker，也不访问真实方舟或 Embedding 网络。
- [x] 数据库集成测试使用独立测试库或事务清理，不破坏开发数据。
- [x] `pytest` 全量通过。
- [x] Ruff 通过。
- [x] mypy 通过。
- [x] 应用导入和数据库模式启动检查通过。
- [x] 仓库没有真实 API Key、数据库密码或生成的数据文件。
- [x] 实际命令、测试数量、migration revision 和尚未实现项写入最终验收报告。
