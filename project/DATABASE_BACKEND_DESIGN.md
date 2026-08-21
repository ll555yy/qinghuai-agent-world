# PostgreSQL + pgvector 后端持久化阶段设计

- 状态：当前 Goal 的实施基线
- 日期：2026-08-19
- 范围：Docker Compose、PostgreSQL/pgvector、SQLAlchemy 2 Async、Alembic、持久化恢复、数据库记忆检索、D-065 与长聊天压缩
- 排除：前端、Neo4j、Redis、消息队列、真实 Embedding 服务和真实方舟自动测试

## 1. 阶段完成定义

本阶段完成后，本地 FastAPI 使用 Docker 中的 PostgreSQL 作为权威持久化来源。创建 Run、推进世界、邀请、加入、聊天、离场沉淀和 Day7 结算产生的状态，在应用进程重建后仍可恢复并继续运行。内存仓储只保留为显式测试实现，数据库配置失败时不得静默退回内存。

同时完成两个已确认但尚未落地的聊天生命周期能力：

1. 不含玩家的 Conversation 连续两次完整调度无人申请发言时，以 `conversation_idle` 关闭并执行正常沉淀。
2. 当前 Segment 过长时生成共享滚动摘要，提示词使用摘要加最近原文，数据库仍永久保留全部 Message。

## 2. 不变量与较新规则

- `RunService` 继续负责世界、会话、草稿、时间和结算规则；SQLAlchemy 模型不能成为第二套领域规则。
- `domain/` 不导入 FastAPI、SQLAlchemy、psycopg、pgvector 或具体模型 SDK。
- D-057 覆盖旧的“NPC 不能离开世界”：NPC 离场沉淀后没有 `active | blocked` Goal 时进入 `departed`。
- D-058 覆盖旧的“第三人直接加入”：第三人必须经过冻结的原参与者一致同意；玩家参与者必须手动表态。
- D-065 只自动收束纯 NPC Conversation；含玩家的 Conversation 继续使用玩家侧空闲入口。
- Memory 查询首先由后端固定 `ownerNpcId`，模型输入无权提供或覆盖 owner。
- Day7 只读取章节权威表，不从 Graph、Goal 或模型临时推测。
- 不在数据库事务或行锁中等待方舟模型调用。

## 3. 本地数据库环境

仓库根目录的 `compose.yaml` 只启动一个 PostgreSQL + pgvector 服务：

- 使用固定的 pgvector PostgreSQL 镜像版本，不使用浮动 `latest`。
- 暴露本地开发端口，配置 `pg_isready` 健康检查。
- 使用命名 Volume；Docker Desktop 已把 Docker 数据根放在 `E:\Docker\Data`，Compose 不再绑定宿主机绝对目录。
- 用户名、密码、库名和端口从 `.env` 读取；仓库只提交 `.env.example`。
- 第一份 Alembic migration 执行 `CREATE EXTENSION IF NOT EXISTS vector`，应用启动不自行建表。

FastAPI 继续在 Conda 环境中运行。推荐开发连接串：

```text
postgresql+psycopg://<user>:<password>@127.0.0.1:<port>/<database>
```

## 4. 配置与依赖注入

`Settings` 增加：

- `persistence_backend: memory | postgres`
- `database_url`
- `database_echo`
- `memory_embedding_dimensions`
- 长聊天压缩阈值与最近原文窗口

应用通过单一 composition root 创建 Repository、DatabaseMemoryRetriever 和可选 EmbeddingPort。规则如下：

- `memory` 必须显式选择，供确定性单元测试使用。
- `postgres` 缺少 URL、连接失败或 migration 未执行时，启动/健康检查给出明确错误，不构造内存 Repository 兜底。
- Engine 与 async session factory 由 FastAPI lifespan 创建和关闭。
- `/health` 返回应用与数据库是否可用，不返回 URL、用户名或密码。

## 5. 数据表

### 5.1 Run 与运行状态

- `chapter_runs`：Run ID、所选 Agenda、世界日/分钟、seed、`state_version`、`event_seq`、各 ID 序号、当前公开世界状态、场景状态、周授权、结局、结束标记、已关闭日期和创建/更新时间。
- `run_actor_states`：当前外部状态、坐标、每日思考分钟、已思考日期、该 Actor 的世界状态和当天 fresh Memory ID。
- `run_daily_schedules`：每日每 NPC 的思考分钟；基准顺序保存在 Run。
- `run_events`：单调 `event_seq`、写入时 `state_version`、类型和公开 payload，用于重连补发。
- `command_records`：`run_id + command_id` 唯一，保存 fingerprint 与返回结果，保证玩家写命令幂等。
- `world_events`：本 Run 已发生的权威事件及可见 Actor、Topic 和中立载荷。

### 5.2 Conversation

- `conversations`：Conversation ID、Run、序号、状态、开始/结束时间和关闭原因。
- `conversation_participants`：参与者、是否当前在场、是否历史参与者和加入顺序。
- `conversation_segments`：Segment ID、Conversation、参与者集合、开始/结束时间、中立摘要、滚动摘要截止 Message。
- `messages`：Message ID、Conversation、Segment、作者、原文、创建时间和 `visible_to_npc_ids`。
- `invitations`、`join_requests`：请求状态和正常业务字段；私有邀请意图只作为私有 JSONB 载荷保存，不进入公开事件。
- `conversation_drafts`：按 `conversation_id + npc_id` 保存 Goal/关系/pending Goal/章节 Effect 草稿。
- `conversation_memory_cache`：按 `conversation_id + npc_id + memory_id` 保存当前私有召回缓存。
- `conversation_idle_states`：纯 NPC 自动空闲计数；关闭后删除或标记结束。
- `consolidations`：按 `conversation_id + npc_id` 唯一，保存状态、原因、尝试次数、草稿是否提交及互动是否记录。

### 5.3 Goal、关系与章节状态

- `goals`：独立字段保存 owner、horizon、disclosure、描述、目标 Actor、Topic、importance、status、父 Goal 和时间/原因。
- `relationships`：`run_id + from_actor_id + to_actor_id` 唯一，保存四个维度、interactionCount 和 socialRoles；数据库 CHECK 约束离散范围。
- `chapter_actor_stances`、`chapter_authorizations`、`chapter_agenda_stances`：保存最新权威值和证据引用。
- `chapter_resolutions`：Day7 固化的公开结果快照。

### 5.4 Memory 与逻辑 Graph

- `topics`：中立正式 Topic 和 aliases。
- `topic_candidates`：尚未满足创建条件的原始候选及提及统计，不属于 Graph 节点。
- `memories`：Memory ID、Run、owner、type、原子内容、importance、confidence、发生/得知/创建/召回时间、source、来源事件/Conversation/Segment，以及 nullable pgvector embedding。
- `memory_evidence_messages`。
- `memory_actor_links`。
- `memory_topic_links`。
- `memory_goal_links`，包含 `evidence | trigger | state_change` role。
- `memory_edges`，边类型限制为 `SUPPORTS | CAUSES | CONTRADICTS | SUPERSEDES | DERIVED_FROM`。

Memory、Goal、Topic、Message 和 Graph 边必须是独立可查询记录。JSONB 只用于结构变化频繁的小型载荷、公开事件 payload、草稿和结局快照；不允许把整个 Run pickle 或单块 JSON 作为唯一权威存储。

## 6. Repository 保存与恢复

现有 `RunRepository` 增加 `save(run)` 和 `healthcheck()`；内存实现保持同样接口。PostgreSQL 实现负责领域对象与关系表之间的映射。

第一版采用“单 Run 行锁 + 单事务同步当前聚合”的清晰路径：

1. 锁定或插入 `chapter_runs` 行。
2. 拒绝数据库中 `state_version` 高于待保存 Run 的过期覆盖。
3. 在一个事务中 upsert 当前权威记录并同步该 Run 的子表。
4. 成功后提交；任一表失败则整批回滚。

当前世界规模只有 6 个 Actor、最多 2 场 Conversation 和七日章节，因此第一版允许对子表使用简单的批量 upsert/同步，不提前实现事件溯源、脏字段追踪或分布式锁。

保存检查点：

- 每个会改变状态的公共命令成功返回前。
- 消息/请求已经写入，而代码准备释放 `run.lock` 等待模型前。
- 模型结果通过版本检查并应用后。
- 离场沉淀、日终和 Day7 结算整体完成后。

不能在持有数据库事务的情况下等待模型。若进程在模型等待期间退出，重启后恢复到“输入已写入、模型结果尚未应用”的稳定检查点，不丢失原始消息。

恢复时从规范化表重建 `Run`、Conversation 和全部私有集合，并重新创建 `asyncio.Lock` 等运行期对象。`active_chat_pipelines`、`in_flight_speech_calls` 和进程内 Lock 不持久化；恢复后把未完成模型流水线视为未运行，不伪造返回。

## 7. 离场事务与幂等性

`ExitConsolidation` 网络调用发生在事务外。模型结果验证完成后，一次 Repository save 事务原子提交：

- 原子 Memory、证据和 Graph 边；
- Goal 正式状态；
- Relationship 正式值和互动计数；
- 新短期 Goal；
- 当前 NPC 的章节立场、授权和 Agenda 态度；
- consolidation 幂等状态和草稿/缓存清理。

`conversation_id + npc_id` 是沉淀幂等键。已成功批次直接返回原结果；失败批次保留消息与草稿，可按现有规则显式重试一次。

## 8. 数据库 Memory 检索

增加异步 `MemoryRetriever` 端口：

```text
search(run_id, owner_npc_id, query, limit) -> MemoryToolResult
```

owner 由绑定 NPC Agent 的运行时注入，不存在于模型 Schema。数据库查询第一层 WHERE 必须包含 `run_id` 与 `owner_npc_id`。

候选流程：

1. Actor、Goal、正式 Topic 与关键词生成精确候选。
2. 配置 EmbeddingPort 且查询向量可用时，使用 pgvector 补充当前 owner 的语义候选。
3. 从主候选沿 Memory 边扩展一至二跳；每一跳继续强制目标 Memory owner 相同。
4. 综合种子命中、向量相似度、Graph 距离、类型、时间、importance、confidence 与新近度排序，最多返回 8 个 ID。

EmbeddingPort 可替换，自动测试使用固定向量，不能访问网络。未配置真实 Embedding 时，embedding 保持 NULL，检索使用关键词 + Graph；不能生成伪向量。第一版固定一种向量维度并建立部分 HNSW 索引，只索引 embedding 非 NULL 的行；更换真实模型维度需要显式 migration。

## 9. 长聊天压缩

默认参数：当前 Segment 超过 20 条尚未摘要的可见 Message，或在存在可压缩前缀时超过约 2400 个本地估算 Token，任一条件满足即触发滚动摘要，保留最近 8 条原文。

- 摘要输入为已有滚动摘要（若有）加本次需要压缩的较早原文。
- 成功后写入 `summary` 与 `summary_through_message_id`。
- 当前参与者的提示词获得“已摘要部分 + 最近原文”；没有亲历 Segment 的后来者不能读取摘要。
- Segment 因加入/离开结束时，用已有滚动摘要加剩余原文生成最终摘要。
- 参与者变化后，继续参与者额外获得上一 Segment 最近 4 条原文作为 `boundaryMessages`；新加入者不能读取该边界尾部。
- 摘要失败不推进 `summary_through_message_id`，保留全部原文并在后续合法触发点重试；聊天继续。

## 10. D-065 自动空闲收束

纯 NPC Conversation 的一次完整 ChatDecision 调度若无人 `speak`，空闲计数加一并触发内部 `conversation_idle` 参与者事件；第二次完整调度仍无人发言时，以关闭原因 `conversation_idle` 走统一的 Segment 摘要、NPC 沉淀和清理路径。

以下行为重置计数：新 Message、有效 NPC 发言、参与者加入或离开。含玩家 Conversation 不自动运行该规则，继续由玩家侧 idle API 显式推进；18:00 对两种会话都强制收束。

## 11. 启动、健康与事件恢复

- FastAPI lifespan 检查数据库连通性和当前 Alembic revision，但不执行 `create_all`。
- `/health` 在数据库模式报告 `database: ok | unavailable`。
- `GET events?afterSeq=` 从 Repository 的持久化 Run Event 查询遗漏事件；EventHub 仍只负责当前进程的实时广播。
- 应用重建后，第一次按 runId 访问从数据库载入并放入当前进程缓存；数据库仍是重启恢复来源。

## 12. 普通失败处理

- 连接失败、migration 缺失、事务失败和过期版本返回明确内部错误，不静默切换状态源。
- 事务失败不发布对应 WebSocket 事件；调用方可重试同一 commandId。
- SegmentSummary 失败保留原文。
- Embedding 不可用退回真实的关键词 + Graph 结果，不伪造向量。
- 不增加 Redis、Neo4j、分布式锁、后台修复平台、多数据库双写或无限重试。

## 13. 实施顺序

1. Compose、依赖、Settings、数据库生命周期、Alembic 和 SQLAlchemy 表。
2. Run 编解码与 PostgreSQL Repository，接入命令保存检查点和恢复。
3. 数据库 MemoryRetriever、EmbeddingPort 与 owner 隔离。
4. D-065 与滚动摘要。
5. migration 往返、重启恢复、事务失败、Graph/向量和完整回归测试。
