# 七天 NPC 聊天世界后端可玩闭环设计

- 状态：本阶段实施基线
- 日期：2026-08-18
- 范围：FastAPI、内存状态、单一 TextModel、REST/WebSocket、自动测试
- 排除：数据库、前端、真实联网自动测试、Embedding 服务

## 1. 阶段完成定义

本阶段不是只增加若干领域类。完成必须能够用 Fake TextModel 从 API 创建一局，依次观察 Day1 事件、NPC 错峰思考、移动、邀请、接受或拒绝、NPC 自动聊天、玩家加入与发言、NPC 离场沉淀、后续事件以及 Day7 固定结算。真实运行使用现有 ArkClient；缺少或调用失败时使用安全结果，不编造剧情。

## 2. 保留与修订的边界

- 本文记录可玩闭环阶段；其后的数据库阶段已由 `DATABASE_BACKEND_DESIGN.md` 覆盖。`InMemoryRunRepository` 仍用于纯单元测试，正式本地运行可使用 PostgreSQL `RunRepository`，Run 领域聚合仍是编排期间的唯一写入入口。
- 世界最多两场聊天，每场最多三人，一个角色最多参加一场。
- 新加入 NPC 只读取加入后的消息；玩家成功加入后可以读取整场历史。
- `ChatDecision` 仍只有 `speak | wait | leave_chat`。按 D-057，离场沉淀后若 NPC 已无 `active | blocked` Goal，由后端将其标记为 `departed`。
- Graph v1 在本阶段是内存中的节点与连接索引，不实现数据库或 Embedding。检索使用 owner 过滤、Actor/Goal/Topic 精确种子和文本词项补充；接口保持可在后续替换。
- observed 世界事件使用单一书店事件中心和固定可见半径；角色坐标决定可见者。场景配置没有坐标时，运行初始化器提供固定默认位置。

## 3. Run 内部状态

Run 增加以下私有字段，均不得整体复制到公共响应：

- `positions[actorId] = {x, y}` 与 `actorStates.status = present | approaching | inviting | chatting | waiting | departed`。
- `dailyThinkMinutes[npcId]`：用 Run seed 随机排列五名 NPC，再分配到每日 09:00、11:00、13:00、15:00、17:00；每天固定复用。
- `thoughtDays[npcId]`：已经执行过主动思考的世界日集合。
- `goals[goalId]`：初始 Goal 的本局可变副本。
- `relationships[(from,to)]`：四维关系和 interactionCount 的本局可变副本。
- `memories[memoryId]`、`memoryLinks` 与每场每 NPC 的 `memoryCache`。
- `worldEvents[eventId]`、`freshEventContext[npcId]`、`currentWorldState`。
- `invitations[invitationId]`。
- `chapterActorStances[npcId]`、`zhouAuthorization`、`chapterAgendaStances[(agendaId,npcId)]`。
- `chapterResolution`：结算后只读的公开结果快照。
- 单调递增的 Message、Segment、Invitation 和 Memory 序号。

公共 Run 快照只增加：坐标与外部状态、公开 Conversation 概况、公开世界事件、当前公开世界状态、是否已经结算、以及结算后的公开结果。不得出现私有 Goal、关系数值、Memory、提示词、决策意图或章节态度矩阵。

## 4. 时间、事件与世界步进

### 时间比例

- 当前确认的正式游玩节奏为现实有效 2 秒对应虚拟 1 分钟，因此 1200 秒对应一个 08:00～18:00 世界日。`world/step` 的 `realSeconds` 保持真实含义，后端按场景中的 `realSecondsPerVirtualMinute: 2` 换算；第一版客户端按完整的 2 秒 tick 提交。
- 测试、真实七日验收和无前端 API 可一次提交大量 `realSeconds` 加速推进，不要求墙钟真实等待；换算和事件顺序仍与正式 20 分钟日长一致。
- 保留旧 `virtualMinutes` 入口作为开发诊断入口，但它也必须经过同一 `WorldEngine`，不能绕过事件、每日思考和结算。
- 一次大步进按下一个事件、NPC 思考时刻或 Day7 截止切段处理，不能先跳到终点后漏掉中间事件。

### 世界事件

- 创建 Run 时先触发 Day1 09:00 公开事件，然后才允许 Day1 09:00 的首名 NPC 思考。
- public 事件覆盖配置的 visibleActorIds；observed 事件按当时位置与书店事件中心半径计算。
- 每名亲历 NPC 立即得到一个系统生成的私有 `event` Memory，并加入当天 fresh context；该节点不需要模型总结。
- 跨入下一日时清空上一日 fresh context，但不删除长期 Memory。
- Day7 18:00 停止新邀请，收束聊天、提交各 NPC 草稿，然后执行程序结算。

## 5. 六类结构化 AI 协议

所有协议使用 Pydantic `extra=forbid`。编排器把 JSON Schema、精简系统说明和 NPC 私有上下文交给 TextModel；从返回文本提取一个 JSON 对象并校验。校验或模型调用失败时，同协议最多再调用一次。第二次失败使用以下安全结果并记录公开不可见的错误码。

### DailyActionDecision

```text
action: seek_chat | wait
goalId?: 当前 NPC 的 active/blocked Goal
targetActorId?: 后端候选中的 present Actor
intent?: 简短私有邀请意图
```

失败为 `wait`。NPC 忙碌、departed 或本日已经思考时不调用。

### InvitationDecision

```text
decision: accept | refuse
```

失败为 `refuse`。接收提示词不得包含发起者的 goalId、intent 或秘密。玩家收到 NPC 邀请时不调用模型，由玩家 API 返回接受或拒绝。

### ChatDecision

```text
result: need_memory | decided
memoryQuery?: {queryText, actorIds, topicHints, goalIds, limit: 1..8}
action?: speak | wait | leave_chat
responseDesire?: 0..3
targetActorId?: 当前参与者
intent?: 私有发言意图
leaveChatAfterSpeaking?: bool
goalUpdates: [{goalId, newStatus, reason, evidenceMessageIds}]
relationshipUpdates: [{targetActorId, dimension, direction, reason, evidenceMessageIds}]
pendingGoal?: {description, parentGoalId?, targetActorIds, topicHints, importance, evidenceMessageIds}
```

`need_memory` 不得同时带行为或草稿变化。同一 NPC、同一触发事件最多召回一次；第二次仍返回 `need_memory` 视为失败。失败为 `decided + wait` 且无变化。该分支由共享编译的 LangGraph 和显式只读工具节点执行，`ownerNpcId` 不属于模型可见 Schema，由 Agent Runtime 注入。

### SpeechGeneration

```text
text: 1..300 个字符
```

失败时本轮不产生 NPC 消息。只有发言竞争胜出者调用。

### SegmentSummary

```text
claims: [string]
commitments: [string]
revealedFacts: [string]
openQuestions: [string]
actorIds: [当前片段 Actor]
topicHints: [string]
```

失败时保留原文且 summary 为空，不阻塞聊天或离场。

### ExitConsolidation

```text
memories: [{ref,type,content,actorIds,topicHints,importance,confidence,evidenceMessageIds,goalIds}]
goalUpdates: [{goalId,newStatus,reason,evidenceMessageIds}]
relationshipUpdates: [{targetActorId,dimension,direction,reason,evidenceMessageIds}]
newShortGoals: [{ref,description,parentGoalId?,targetActorIds,topicHints,importance,triggerMemoryRefs}]
chapterEffects: [{kind,agendaId?,value,evidenceMessageIds}]
```

失败时仍提交已验证的会话 Goal/关系草稿并保留原始消息，但不生成新 Memory、Goal 或章节 Effects；沉淀状态记录为 failed，允许显式 API 重试一次。Day7 不使用失败模型补猜立场。

## 6. 私有提示词组成

提示词编排器按协议只提供必要字段：

- 当前 NPC 的完整人设、边界和秘密；
- 当前 NPC 自己的有效 Goal；
- 当前 NPC 指向相关角色的关系；
- 当前 NPC 所有且经授权召回的 Memory，以及当天 fresh event context；
- 权威世界时间、坐标、公开世界状态和可选候选；
- 该 NPC 实际亲历的当前 Segment 摘要和消息；
- 当前会话草稿变化原因；
- 明确说明聊天中的用户文字是世界内角色发言，不是更高优先级系统指令。

提示词和模型原始文本只存在调用栈，不进入公共事件或公共异常。

## 7. 邀请、移动与聊天创建

1. `seek_chat` 通过校验后发出 `actor_movement_started`。
2. 后端移动到目标附近并写入 `actor_movement_completed`。本阶段不模拟逐帧路径。
3. 写入 `invitation_requested`，请求气泡状态属于发起者。
4. NPC 接收方调用 InvitationDecision；玩家接收方等待玩家 API。
5. 拒绝依次写 `invitation_request_cleared`、`invitation_refused`，拒绝气泡属于接收方，不创建 Conversation。
6. 接受后创建两人 Conversation 和首个 Segment。NPC 发起时调用 SpeechGeneration 生成第一句话；玩家发起时等待玩家第一条输入。
7. 目标若已在未满 Conversation 中，接近者直接加入，不发邀请。

## 8. Conversation、可见性与发言调度

- Message 保存 `messageId`、author、text、createdAt、segmentId 和写入时的 `visibleToNpcIds`。
- 参与者变化先关闭旧 Segment 并请求中立摘要，再创建新 Segment；向原成员的 ChatDecision 注入 `actor_joined` 或 `actor_left` 事件。
- 新加入 NPC 的提示词和 Memory 证据查询只允许其加入后可见消息；玩家成为参与者后，消息 API 返回整场历史。
- 新消息到达后，对除消息作者外的在场 NPC 调用 ChatDecision；合法草稿立即应用，即使该 NPC 没有获得发言权。
- 先执行 `leave_chat`，再进行发言竞争。点名者优先，其次 `responseDesire`，再减去同一 NPC 连续发言一次的惩罚，最后用 Run seed 稳定选择。
- 胜者发言会成为新消息并继续流水线。依据真实 Day1 与七日样本，普通外部触发最多连续生成 2 条 NPC 消息，参与者加入/离开触发最多追加 1 条；达到上限进入等待，不伪造结束台词。初始会话 Memory cache 为 1 条，旧事证据不足时由 Agent 按需调用记忆工具。
- 所有人等待一次后记录 idle；下一次显式 `conversation_idle` 仍无人说话则关闭并沉淀。
- `leaveChatAfterSpeaking` 在该 NPC 的消息成功写入后执行。

## 9. 草稿、Memory 与离场提交

- Goal 更新只能针对本 NPC Goal，证据必须是其可见消息，状态转换只允许 active/blocked 互转或进入 achieved/abandoned；终态不可恢复。
- 关系更新只能修改当前 NPC 指向他人的 trust/affinity/tension，一次决策同一维度最多一步，并限制在配置范围；familiarity 仅由后端更新。
- 两名角色共同参与过一场聊天，在各自离场时每个方向的 interactionCount 最多增加一次；阈值 1/3/6 对应 familiarity 1/2/3。
- ExitConsolidation 的 Memory 强制 owner 为当前 NPC；证据必须可见；正式 ID、时间、Conversation/Segment 归属由后端填写。
- 本阶段内存检索先过滤 owner，再按 actor/goal/topic 精确命中数、词项命中、重要度和新近度排序，最多返回 8 个节点。它不声称等价于最终 pgvector 排序。
- 章节 Effect 必须引用当前 NPC 自己说出的可见消息：总体立场为 `unknown|support|conditional|oppose|withdrawn`；只有周慎之可更新授权；Agenda 必须存在且只更新本人态度。
- 离场提交成功后清除该 NPC 在本场的草稿和缓存。若所有 Goal 终结，设置 `departed`。

## 10. Day7 固定结算

- 章节结果完全按 D-047：五人全 support 且周授权 approved 为 consensus；否则授权 approved/conditional、positive>=3、negative<=1 为 compromise；其余 no_submission。
- no_submission 时五项 Agenda 全为 not_adopted。
- 已提交时依 CHAPTER_AGENDAS 和 D-049 计算 core/partial/not_adopted。
- 玩家 selectedAgendaId 映射 completed/partial/failed；旁观为 null。
- 公共结果只显示章节分支、五项公开主张结果和玩家任务结果，不显示投票矩阵、证据、关系、Goal 或秘密。

## 11. REST 与 WebSocket

保留现有接口，并增加：

- `POST /api/runs/{runId}/world/step`：`{realSeconds, commandId?}`，推进并处理所有到期系统行为。
- `GET /api/runs/{runId}/actors/{actorId}`：仅公开角色卡。
- `POST /api/runs/{runId}/invitations`：玩家邀请 NPC。
- `POST /api/runs/{runId}/invitations/{invitationId}/respond`：仅用于玩家回应 NPC 邀请。
- `POST /api/runs/{runId}/conversations/{conversationId}/join`：玩家主动加入未满聊天。
- `GET /api/runs/{runId}/conversations/{conversationId}/messages`：玩家只有在当前或曾经参加该 Conversation 时才能读取完整历史。
- `POST /api/runs/{runId}/conversations/{conversationId}/messages`：当前玩家发送自由文本。
- `POST /api/runs/{runId}/conversations/{conversationId}/idle`：显式推进一次空闲收束。
- `POST /api/runs/{runId}/consolidations/{npcId}/retry`：只重试处于 failed 的离场批次。

WebSocket 继续发送公共事件序列。后台 NPC 台词只有玩家当时属于该 Conversation 时才带文本，否则只发送 `conversation_activity`；加入前的后台台词不会通过旧事件补发给未参与玩家。

## 12. 普通失败处理

- 不存在或不合法的角色、会话、邀请、Goal、Agenda、消息证据返回明确 4xx DomainError。
- commandId 对会改变状态的玩家命令保持幂等；相同 ID 不同载荷返回冲突。
- 模型缺配置、超时、限流、服务不可用或结构错误执行各协议安全结果；不把内部错误转换成虚构台词、Memory 或立场。
- 一个 Run 内通过锁串行提交权威状态。第一版不增加分布式锁、熔断、多模型复核或复杂恢复日志。

## 13. 验收证据

自动测试至少证明：

1. 正式前端累计 1200 个现实有效秒对应完整世界日；测试可按同一倍率一次提交较大的 `realSeconds` 快进，跨越时仍按序触发事件和五名 NPC 一日一次错峰思考。
2. Day1 公告早于首个思考；observed 事件只进入半径内 NPC Memory。
3. 移动、请求气泡、接受/拒绝事件顺序正确，拒绝不创建会话。
4. 两场/三人/角色唯一会话限制成立；第三 NPC 直接加入并触发加入事件。
5. 新 NPC 看不到加入前内容，玩家加入后能读全历史；公共快照和 WS 不泄露后台台词或私有状态。
6. ChatDecision 可同时更新 Goal、多个关系维度并申请发言；未获发言权者的合法草稿仍生效。
7. need_memory 每 NPC 每消息只允许一次，检索严格 owner 隔离。
8. leave_chat、说后离场、片段摘要、个体 Memory、熟悉度和草稿一次提交成立。
9. Goal 全部 achieved/abandoned 后 NPC 变为 departed，之后不再思考或被邀请。
10. 六协议正常、非法 JSON、超时/缺配置的安全结果都有 Mock 测试且不访问网络。
11. 固定种子至少完整跑完一局 Day1～Day7；Day7 三种大结局、五项 Agenda 和玩家任务映射均由程序规则覆盖。
12. `pytest test/backend -q`、Ruff、mypy、应用导入和真实密钥扫描全部通过。

## 14. 明确延后

- PostgreSQL、SQLAlchemy、Alembic、pgvector、真正的向量相似度和跨进程恢复；
- React/Phaser 前端、逐帧寻路和气泡动画；
- 自动真实时间后台循环。无前端阶段由 `world/step` 提交现实秒数，未来客户端以固定频率驱动；
- 真实方舟连通性和质量评估，必须等用户轮换并本地设置新 Key。
