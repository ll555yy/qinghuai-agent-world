# 消息驱动的并行聊天轮次设计

- 状态：已实现并完成本地验收；实库 PostgreSQL 门禁受本机 Docker 环境限制，详见实施计划第 12 节
- 日期：2026-08-26
- 范围：Conversation 内的开场、加入、并行决策、多人回复、自然展示、冷场复询与离场
- 不变边界：保留现有 `NPCAgent`、LangGraph 记忆召回、私有可见性、会话草稿、`ExitConsolidation` 和 PostgreSQL 权威状态

## 1. 目标

把当前“逐个询问 NPC、只选择一人发言、按世界时钟约 4 秒续聊”的机制，改成由新消息触发的并行分轮机制：同一轮内所有符合条件的 NPC 并行判断，多名 NPC 可以共同回复；整轮完成后，新产生的消息再触发下一轮。

这次改造只改变聊天编排与展示节奏，不重写决策、检索、记忆、Goal、关系或章节结算框架。

## 2. 核心不变量

1. 同一 Conversation 同时只能有一个活动轮次；不同 Conversation 可以并行执行。
2. 同轮 NPC 使用同一份冻结输入快照，互相看不到尚未提交的同轮台词；下一轮才能看到上一轮完整结果。
3. ChatDecision 与 SpeechGeneration 可以并行调用，但所有状态应用和消息提交都必须重新取得 `Run.lock` 并串行完成。
4. 模型等待期间不得持有数据库事务或 `Run.lock`。
5. 每个 NPC 每轮最多产生一条台词，可以综合回应本轮的多条新消息。
6. 同轮所有决定先收集并校验，再合并到各 NPC 的 `conversation_drafts`；不得按 Provider 返回先后修改草稿。
7. `conversation_drafts` 可以持久化以支持恢复，但正式 Goal、关系、章节状态仍只在 NPC 离场或会话关闭的 `ExitConsolidation` 中幂等提交。
8. 玩家消息写入后立即出现在 UI；不得等待 NPC 回答后才显示。玩家离开也立即生效，不等待聊天轮次结束。
9. 新消息、参与者变化和 Day7/18:00 边界都必须有版本校验；过期模型结果不能写消息或草稿。

## 3. 术语与轮次状态

### 3.1 消息批次

一轮的触发输入是 `triggerMessageIds`：

- 通常是一条玩家或 NPC 新消息；
- 上一轮有多名 NPC 发言时，是上一轮按固定顺序提交的全部消息；
- 活动轮次期间玩家追加的消息进入 `queuedMessageIds`，当前轮完成后与当前轮输出共同组成下一轮输入。

### 3.2 状态机

```text
idle
  ├─ new_messages ─→ deciding ─→ generating ─→ publishing ─→ idle
  │                                      │
  │                                      └─ queued_messages ─→ deciding
  └─ no_speaker ─→ cooldown ─→ final_check ─→ speaking | closing
```

建议的持久化状态至少包含：

- `roundId`、`status`、`roundVersion`；
- `triggerMessageIds`、`queuedMessageIds`；
- 当前 Segment/参与者版本；
- 冷场截止时间和是否已经执行最后复询；
- 已生成但尚未发布的消息及其稳定顺序（如采用后端逐条发布）。

进程重启时，`deciding` 或 `generating` 不复用在途 Provider 调用，而是依据持久化触发消息安全地重新排队；已发布消息不能重复生成或重复提交。

## 4. 开场与加入

### 4.1 发起聊天

- NPC 发起并获接受：发起 NPC 必须先生成开场白；开场白作为第一轮触发消息，其他 NPC 再判断是否回复。
- 玩家发起并获接受：玩家输入的第一句话是开场白；系统不能替玩家编造台词。在第一句话到达前，会话保持 `awaiting_player_opener`，NPC 不抢先说话。

### 4.2 加入已有聊天

- NPC 加入：加入者先基于自己获准看到的新 Segment 上下文生成一句入场发言；随后其他 NPC 对该发言进行判断。
- 玩家加入：玩家成功加入后先输入一句话；系统不能自动替玩家发言。在此之前原会话不因玩家加入而触发“欢迎玩家”轮次。
- 参与者变化仍关闭旧 Segment 并创建新 Segment；新加入 NPC 不得读取加入前的原始消息或旧 Segment 摘要。

“加入者先说话”是编排规则。NPC 的入场台词仍走结构化意图与 SpeechGeneration，不绕过人设、可见性和记忆边界。

## 5. 普通消息轮次

收到一批新消息后：

1. 冻结 `runId + conversationId + roundId + roundVersion + segmentId + participantVersion + triggerMessageIds`。
2. 计算符合条件的 NPC：当前仍在场的全部 NPC 都可判断；如果本批只有该 NPC 自己的消息且没有其他新内容，则该 NPC 不参加。若批次含多名作者，刚发过言的 NPC 仍可回应别人。
3. 为每名 NPC 按其私有可见范围构造上下文，并异步并行执行完整 `NPCAgent.chat_message_received`。需要记忆时，仍由现有 LangGraph 工具节点召回 owner 私有 Memory 后再次决策。
4. 等待本轮全部判断完成或单项超时。失败、超时或无效结果按该 NPC 本轮 `wait` 处理，不拖死其他 NPC。
5. 在持锁区重新校验轮次、Segment、参与者和 Day7/18:00 边界。只有仍然有效的决定才能统一校验并合并到各自会话草稿。
6. 立即处理合法 `leave_chat`；对全部合法 `speak` 决定并行执行 SpeechGeneration，不再只选一名胜出者。
7. 收集全部台词后再次做 stale、非空、重复和证据校验，然后按稳定顺序发布。
8. 本轮发布完毕后，把本轮消息和活动期间排队的玩家消息组成下一轮输入；若没有新消息则进入空闲。

提示词应要求：有直接问题、未回应的玩家表达、未解决分歧、可推进 Goal 或新信息时尽量回复；只能重复、附和或回应自己时选择 `wait`。直接点名提问仍必须作答，但可以拒绝请求或表达反对。

## 6. 并发与限流

### 6.1 并发层级

```text
会话 A：NPC 1 / NPC 2 并行 ChatDecision
会话 B：NPC 3 / NPC 4 并行 ChatDecision
                         ↓
统一经过 Provider 物理请求信号量
```

- 用每会话运行时锁或任务状态替代当前 Run 级 `chat_pipeline_lock` 的全局串行效果。
- `Run.lock` 只保护短暂的状态读取、验证、草稿合并、消息写入和持久化，不覆盖网络等待。
- 增加进程级、可配置的 Provider 信号量，初始默认值为 6；它限制 ChatDecision、记忆后第二次决策、SpeechGeneration、摘要和沉淀等所有物理模型请求，而不是只限制顶层 NPC 数。
- 不同会话的模型等待可以重叠，但共享 Run 的最终状态应用仍由 `Run.lock` 串行化，避免 PostgreSQL revision 冲突和内存丢写。

### 6.2 超时和局部失败

- 单项模型调用沿用协议级超时与一次格式重试。
- 一名 NPC 失败只降级为该 NPC 本轮 `wait` 或无台词；不取消已经成功的其他 NPC。
- 如果整轮所有 SpeechGeneration 都失败，按“本轮无人发言”进入冷场规则。
- 全局限流不得长期占用会话锁；等待信号量期间仍允许另一 Conversation 推进和玩家立即离开。

## 7. 稳定顺序与自然展示

模型判断和台词生成并行，但展示顺序不能由网络返回速度决定。稳定排序为：

1. 本轮被直接点名或直接提问的 NPC；
2. `responseDesire` 从高到低；
3. Conversation 加入顺序；
4. `actorId` 作为最终稳定 tie-break。

消息按该顺序逐条发布。相邻 NPC 台词使用确定性的自然间隔：默认 1.2～3.0 秒，依据文本长度、`roundId` 和固定 seed 计算，不使用不可复现的系统随机数。

为了让 WebSocket 客户端真实地逐条收到消息，后端每次发布前必须完成消息持久化，再广播对应 `message_created`；不能等整轮结束后一次性广播全部事件。发布间隔期间不持有 `Run.lock` 或数据库事务。

玩家在展示期间的新消息立即持久化、广播并进入 `queuedMessageIds`。它不会取消已经生成的当前轮结果，也不会插入另一个重叠轮次；当前轮发布结束后，系统以排队消息和本轮 NPC 消息启动下一轮。

## 8. 冷场、复询与离场

当一轮没有任何合法发言者时：

1. Conversation 进入 `cooldown`，等待 10～15 秒；建议默认 12 秒，并保留配置项。
2. 等待期间任何玩家新消息都会立即取消计时、清零冷场状态，并直接启动新的普通消息轮次。
3. 计时结束仍无新消息时，当前 NPC 并行执行一次 `final_check`。提示词明确这是最后回应机会：有值得承接的内容就说，否则自然离场。
4. `final_check` 有人说话：按普通发布规则处理，并以这些新消息继续下一轮。
5. `final_check` 仍无人说话：所有仍选择 `wait` 的 NPC 被编排器转换为离场；已经选择 `leave_chat` 的 NPC 正常离场。NPC 可以在最后判断中选择“说一句结束语后离开”。
6. 玩家仍在场时不强制玩家离开；所有 NPC 离开后 Conversation 按现有最少参与者规则关闭。

固定约 4 秒的 Conversation 自主轮询被取消。世界时钟仍可触发“NPC 主动发起全新话题”等独立世界行为，但不能用旧消息反复唤醒已暂停聊天。

## 9. 防失控规则

“尽量回复”不能变成无限机械接龙：

- 提示词禁止只重复、改写或无意义附和上一轮。
- 同一 NPC 的近重复台词在写入前降级为 `wait`。
- 连续 NPC-only 轮次达到可配置的安全预算时，不强制关闭对话，而是主动进入一次冷场周期；这只是资源保护和调度让步，不是用户可见的固定回复上限。玩家新消息会重置该安全预算。
- Day7/18:00 仍是硬边界：已授权的台词可以按现有规则落地，之后不再开启新轮次并执行正常沉淀。

## 10. 草稿与正式状态

本方案直接复用现有机制：

```text
整轮并行决定
→ 统一校验
→ 合并进各 NPC 的 conversation_drafts
→ 草稿持久化（尚未生效）
→ NPC 离场 / Conversation 关闭
→ ExitConsolidation
→ 幂等提交正式 Goal、关系、Memory 与章节状态
```

同一轮不同 NPC 的草稿属于各自私有键，不需要相互读取。Provider 返回顺序不能改变草稿内容；若同一 NPC 在后续轮次更新同一 Goal，则继续沿用现有草稿覆盖与证据校验规则。

## 11. API 与前端影响

- 玩家发送接口必须先确认消息写入并尽快返回或提供已接受状态；NPC 轮次不能阻塞玩家消息在 UI 中出现。
- 玩家离开接口立即修改参与者状态并广播，活动轮次随后通过版本校验丢弃该玩家离开后不再合法的结果。
- 建议在消息/事件中增加 `roundId`、`roundSequence` 和可选的 `replyToMessageIds`，便于前端稳定去重、排序和调试；这些字段不能泄露私有提示词或 Memory。
- 前端继续支持 Enter 发送、Shift+Enter 换行和乐观玩家消息；对服务端消息按事件到达顺序展示，不自行重排 Provider 结果。

## 12. 明确不修改的部分

- 六类 AI 协议及 `ChatDecision -> need_memory -> retrieve_owned_memories -> decided` 主流程；
- NPC persona/coreSecrets、可见消息边界和 owner 私有 Memory；
- Conversation/Segment 的可见性语义；
- Goal、关系、pending Goal、chapterEffects 草稿 Schema；
- `ExitConsolidation`、幂等键和正式状态提交时机；
- PostgreSQL + pgvector、Graph/向量混合检索和 Day7 结算来源；
- 单场最多三人、世界最多两场会话等玩法约束。

## 13. 验收标准

1. 同一会话内两名 NPC 的 ChatDecision 在测试屏障上真实重叠，而非逐个 await。
2. 两场 Conversation 的模型调用可以重叠，且共享 Run 不丢状态、不产生 revision 冲突。
3. 一轮两名 NPC 都选择 `speak` 时，两条台词都生成、按稳定顺序逐条持久化和广播。
4. 下一轮上下文包含上一轮全部已发布消息，不包含尚未发布或其他 NPC 不可见的内容。
5. 玩家在 deciding/generating/publishing/cooldown 任一阶段发言都立即可见，并在正确的下一轮只处理一次。
6. 冷场等待期间玩家发言会取消计时并重置；无人发言时只复询一次，之后 NPC 正常离场并沉淀。
7. 并行决定只写草稿；正式 Goal、关系和章节状态在离场前保持不变，离场后只提交一次。
8. 单项超时、格式错误、SpeechGeneration 失败、参与者变化、进程恢复和 18:00 边界都有确定性测试。
9. 前后端现有测试、Ruff、mypy、lint、typecheck、构建及适用的 PostgreSQL/Playwright 门禁通过。
