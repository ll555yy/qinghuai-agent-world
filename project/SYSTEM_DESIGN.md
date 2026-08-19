# 第一版系统设计

- 状态：基于已确认玩法的实施设计
- 技术栈：已确认，见 `PROJECT_DESIGN.md` D-052 与 `TECH_STACK.md`
- 范围：单场景、五名 NPC、一名玩家、最多两场并行聊天、Day1～Day7 单章节

## 1. 核心不变量

1. NPC 只能读取自己的私有状态、自己亲历的聊天和后端授权召回的私有 Memory。
2. 模型只返回语义决策，不生成正式 ID、权威时间、数据库边或最终数值。
3. 原始消息和权威世界事件不可变；角色说法、belief 和客观事实分层保存。
4. 同一角色同一时间最多参加一场 Conversation；单场最多三人；世界最多两场。
5. Goal 与关系在聊天内先写会话草稿，NPC 离场时一次性提交正式状态。
6. Graph 只服务长期记忆检索；章节结局只读取结构化章节状态表。
7. 玩家任务选择不会直接修改 NPC 状态，玩家只能通过聊天间接影响结果。

## 2. 系统边界

### 客户端

- 渲染二维场景、头像、移动、聊天圈和气泡。
- 提供右键角色菜单、公开资料、邀请、加入、聊天面板、暂停、前情、任务选择和结算界面。
- 发送玩家意图，不在客户端判定 NPC Goal、关系、记忆或结局。

### 世界编排服务

- 维护权威世界时间、角色坐标、世界事件、每日思考和最多两场 Conversation。
- 执行邀请、参与者加入离开、发言调度、暂停令牌、空闲收束和 Day7 截止流程。
- 为每次异步模型调用附加 `runId`、`conversationId`、`eventSeq` 和 `stateVersion`，拒绝过期结果。

### AI 编排服务

- 组装每名 NPC 的私有提示词。
- 调用六类模型协议：`DailyActionDecision`、`InvitationDecision`、`ChatDecision`、`SpeechGeneration`、`SegmentSummary`、`ExitConsolidation`。
- 使用结构化 Schema 校验结果；失败时只做一次同协议重试，仍失败则执行协议对应的安全默认值。

### 持久化与检索服务

- 保存权威运行状态、原始消息、会话草稿、Goal、关系、Memory、Topic、逻辑 Graph 边、向量和章节状态。
- 强制 `ownerNpcId` 权限过滤，模型不能传入或覆盖所有权条件。
- 提供按 ID 精确查询的章节结算，以及 Graph 邻接与向量混合的长期记忆召回。

## 3. 状态分层

| 层 | 示例 | 权威来源 | 写入时机 |
|---|---|---|---|
| 世界事实 | 时间、坐标、事件、截止时间 | 后端 | 世界 Tick 或脚本事件 |
| 正式角色状态 | Goal、关系、长期 Memory | 数据库 | 初始化或离场事务 |
| 会话私有草稿 | Goal 覆盖、关系覆盖、pending Goal、记忆缓存 | 当前 Conversation | 每次合法 `ChatDecision` |
| 原始对话 | 消息、加入离开事件、可见片段 | 消息存储 | 事件发生时立即追加 |
| 章节精确状态 | 提交立场、周授权、Agenda 态度 | 章节状态表 | 离场 `chapterEffects` 提交 |
| 玩家展示 | 公开资料、聊天记录、世界事件、任务结果 | 后端裁剪后返回 | 按 UI 事件 |

## 4. 世界时间与事件

- 每日运行窗口为 08:00～18:00；正常运行时每现实有效分钟推进一虚拟小时。
- Day1 在前情和任务选择结束后从 09:00 开始；每天 18:00 直接跳到下一天 08:00。
- 玩家主动暂停、应用离线和玩家当前聊天的前台 AI 决策周期不计入有效时间。
- 后台 NPC Conversation 的 AI 请求不暂停时间；返回时必须通过版本校验。
- `public` 事件直接覆盖配置中的全部角色；`observed` 事件按坐标、事件区域和可见半径计算角色集合。
- 世界事件先写权威 Event，再为每名亲历 NPC 创建私有 `event` Memory，并加入当天 `freshEventContext`。

## 5. 角色世界状态机

```text
idle
  ├─ DailyActionDecision=wait ───────────────→ idle
  ├─ seek_chat(target idle) ─→ approaching ─→ invitation_pending
  └─ seek_chat(target in open chat) ─────────→ approaching_to_join

invitation_pending
  ├─ accept ─→ in_conversation
  └─ refuse ─→ idle_for_day

approaching_to_join
  ├─ slot still available ─→ in_conversation
  └─ state changed/full ───→ idle_for_day

in_conversation
  └─ leave_chat / conversation closed ─→ idle
```

- NPC 主动找空闲目标时需要邀请；目标在未满 Conversation 中时，NPC 到达后作为第三人直接加入。
- 本日主动邀请失败后不再寻找第二目标，但仍可被邀请或作为第三人加入。
- NPC 不存在 `leave_world` 或永久退出状态。

## 6. Conversation 生命周期

```text
creating → opening → active ↔ waiting_ai → idle_pending → closing → closed
```

- NPC 发起并获接受时，由发起 NPC 生成开场台词；玩家发起并获接受时，由玩家输入第一句话。
- 每次参与者集合变化立即关闭当前 ConversationSegment，创建新 Segment；新加入 NPC 只读取新 Segment，玩家 UI 可以读取 Conversation 全历史。
- NPC `leave_chat` 时先关闭其共同可见区间，再执行该 NPC 的 `ExitConsolidation`；其他参与者可以继续。
- 玩家离开只产生参与者事件和 Segment 边界，不强制剩余 NPC 立即沉淀。若剩余不足两人，Conversation 关闭，剩余 NPC 执行沉淀。
- 所有 NPC 选择等待后进入 `idle_pending`。达到空闲阈值触发一次 `conversation_idle`；仍无人发言则关闭会话。
- Conversation 关闭或参与者变化时，需要摘要的已结束 Segment 进入 `SegmentSummary` 队列；摘要只属于可见集合，不带单一 `ownerNpcId`。

## 7. 新消息处理流水线

1. 把玩家/NPC 消息或参与者事件追加到 Conversation，生成单调递增的 `eventSeq`。
2. 对当前每名 NPC 组装其实际可见上下文，并并行调用 `ChatDecision`。
3. 返回 `need_memory` 时，后端按当前 NPC 所有权执行一次混合检索，再针对同一 `eventSeq` 调用第二次决策。
4. 校验 `goalUpdates`、`relationshipUpdates` 和 `pendingGoal`，写入当前 NPC 的会话私有草稿。
5. 先处理立即 `leave_chat` 的 NPC，再从剩余 `speak` 申请中按点名、`responseDesire`、连续发言惩罚和稳定随机种子选一人。
6. 只有胜出者执行 `SpeechGeneration`；输出文本写入新消息并开始下一轮。
7. 若无人发言，则本轮完成并进入等待或空闲收束。玩家触发的前台暂停令牌在此时释放，不要求必须生成台词。

所有异步返回都必须匹配当前 `runId + conversationId + eventSeq + stateVersion`；不匹配的结果记录为 stale 后丢弃，不能修改状态。

## 8. 六类 AI 调用职责

| 调用 | 触发 | 只负责 | 不负责 |
|---|---|---|---|
| DailyActionDecision | 每 NPC 每日一次错峰 | 找谁聊天或等待 | 移动、邀请接受、台词 |
| InvitationDecision | 收到邀请 | 接受或拒绝 | 台词、推迟状态 |
| ChatDecision | 新消息/参与者事件 | 召回需求、行为、草稿语义变化 | 最终台词、数据库写入 |
| SpeechGeneration | 发言竞争胜出 | 一句角色台词 | 再次修改 Goal/关系 |
| SegmentSummary | 片段关闭或超长 | 中立可见摘要 | NPC 主观解释 |
| ExitConsolidation | NPC 离场/会话关闭/截止 | 原子 Memory、新短期 Goal、变化记忆、章节 Effects | 重复应用 Goal/关系数值 |

## 9. 长短期上下文

- 原始消息永久保存，模型上下文只使用“较早可见 Segment 摘要 + 最近可见原文”。
- NPC 进入 Conversation 时以在场人物、当前 Goal 和开场 Topic 初始化私有 Memory 缓存。
- 新消息只有在现有缓存不足时允许一次新增召回；无结果时角色表现为不知道或不确定。
- 召回先由 Actor/Goal/Topic/已知 Memory 多种子生成 Graph 候选，再由当前 NPC 私有向量候选补充；排序后扩展有限前因、矛盾和取代关系。
- Topic 是全局中立索引；Memory 始终按 `ownerNpcId` 私有。

## 10. 逻辑数据模型

### 权威与内容表

- `chapter_runs`：本局、当前时间、暂停状态、所选 Agenda、结局。
- `actors`、`npc_profiles`：公开身份和私有人设分表。
- `world_events`、`world_state`、`actor_positions`。
- `goals`、`relationships`、`topics`。
- `conversations`、`conversation_participants`、`conversation_segments`、`messages`。

### 会话状态表

- `conversation_goal_drafts`。
- `conversation_relationship_drafts`。
- `conversation_pending_goals`。
- `conversation_memory_cache`。
- `model_calls`：协议、输入版本、状态、重试和耗时；不把完整秘密提示词发送到客户端日志。

### Memory 与逻辑 Graph

- `memories`：owner、类型、原子内容、时间、重要度、可信度和向量。
- `memory_evidence_messages`。
- `memory_actor_links`、`memory_topic_links`、`memory_goal_links`。
- `memory_edges`：`SUPPORTS | CAUSES | CONTRADICTS | SUPERSEDES | DERIVED_FROM`。
- `topic_candidates`：Graph 外候选及提及统计。

### 章节状态

- `chapter_actor_stances`：每名 NPC 的最终提交立场。
- `chapter_authorizations`：周慎之授权。
- `chapter_agenda_stances`：每名 NPC 对每项 Agenda 的态度。
- 每条状态保存 `sourceMessageId`、`sourceMemoryId`、`effectiveAt` 和 `supersedesId`。

## 11. NPC 离场事务

单个 NPC 离场时执行一个幂等事务：

1. 锁定 `conversationId + npcId` 的离场批次，重复请求直接返回既有结果。
2. 关闭该 NPC 的共同可见区间并固定可读消息集合。
3. 调用或读取已完成的 `ExitConsolidation`。
4. 校验证据可见性、Goal 所有权、关系方向、临时引用和 `chapterEffects` 本人发言要求。
5. 创建 Memory、证据链接、Topic/Goal/Actor 链接和 Memory 边。
6. 仅按会话草稿最终值提交正式 Goal 与关系一次，并记录对应变化 Memory。
7. 创建有效的新短期 Goal。
8. 更新总体立场、周授权和零到多项 Agenda 态度。
9. 更新熟悉度互动计数，清除该 NPC 会话草稿和缓存，提交事务。

## 12. Day7 结算

1. 停止新邀请和 Conversation。
2. 允许已经开始的当前台词完成，之后关闭全部 Conversation。
3. 等待所有 NPC 离场事务完成；失败批次按同一幂等键重试一次。
4. 精确读取五人提交立场和周慎之授权，计算共识、妥协或未提交。
5. 未提交时五项 Agenda 全部未采纳；已提交时按五人 Agenda 态度矩阵计算核心、部分或未采纳。
6. 根据玩家 `selectedAgendaId` 映射私人任务结果。
7. 固化结局快照，停止世界时间并展示公开结果；不读取 Graph 推测结局。

## 13. 最小失败处理

- 结构化输出校验失败：同协议重试一次；仍失败则使用不产生剧情写入的安全结果。
- DailyActionDecision 失败：`wait`。
- InvitationDecision 失败：`refuse`。
- ChatDecision 失败：`wait`，不修改草稿。
- SpeechGeneration 失败：本轮无人发言，进入等待；不伪造台词。
- SegmentSummary 失败：保留原文，延后重试，不阻塞聊天。
- ExitConsolidation 失败：不丢弃草稿和消息，标记待重试；Day7 必须完成或以已有已验证草稿提交，不能由失败模型凭空补状态。
- 所有写入使用幂等键，过期 AI 返回只能记日志，不能重新应用。

## 14. 验证方案

### 确定性测试

- 每个状态机的合法与非法转换。
- 三人上限、两场上限和角色唯一会话约束。
- 玩家/NPC 加入前消息可见性差异。
- Goal/关系草稿只提交一次。
- `chapterEffects` 只能修改本人状态且证据可见。
- Day7 三分支和五项 Agenda 采纳矩阵。
- 暂停、离线、前台等待和跨日事件调度。

### AI 模拟测试

- 以固定随机种子自动跑完整七日，分别运行玩家旁观和五个支持任务。
- 检查死锁聊天、重复邀请、台词循环、Memory 重复、秘密越权、关系跳变和无证据结局。
- 记录每局六类调用次数、输入输出 Token、延迟、重试率和结局分布。

## 15. 已确认的技术实现基线

- 客户端使用 React + Vite + TypeScript；Phaser 3 承载二维场景，React 承载聊天和管理界面。
- Python 后端使用 FastAPI + Uvicorn，Pydantic v2 统一 HTTP、WebSocket、配置和模型输出契约。
- PostgreSQL + pgvector 同时承载关系数据、Memory Graph 边和向量；SQLAlchemy 2 Async + psycopg 3 负责访问，Alembic 管理迁移。
- Python 使用独立 Conda 环境和 `environment.yml`；前端使用 pnpm。
- 第一版是单体权威后端，使用 `asyncio.Queue` 和 `asyncio.Semaphore` 编排，不引入 Neo4j、Redis 或独立消息队列。
- 模型通过火山方舟 Agent Plan 的 OpenAI 兼容接口调用；初期模型为 `doubao-seed-2.0-lite`，具体提供方只存在于适配器层，路由保持配置化。
