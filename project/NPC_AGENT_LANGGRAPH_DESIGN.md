# NPC Agent 化与 LangGraph 工具式记忆召回设计

状态：已确认阶段 Goal 的实施设计

日期：2026-08-19

范围：仅迁移现有内存后端的 NPC 决策编排；不接数据库、前端、MCP、Embedding 或新的剧情配置。

## 1. 迁移目标与不变量

本阶段把五名 NPC 从“`RunService` 直接调用模型协议”迁移为五个独立的逻辑 `NPCAgent`。每个 Agent 绑定唯一 `actorId`、自己的私有提示上下文、自己的 Memory Cache 视图和允许使用的内部工具；五个 Agent 继续共享一个 `DecisionService` 和一个 `TextModel`，不创建五个模型客户端或常驻进程。

迁移后仍满足以下不变量：

- `Run` / `RunService` 是世界状态唯一权威，Agent 只返回语义决定和只读工具结果。
- 时间、候选过滤、移动、邀请事件顺序、Conversation 创建与三人上限、唯一发言者选择、草稿校验与提交、离场、Day7 结算全部留在 `RunService`。
- 六类协议继续独立存在：`DailyActionDecision`、`InvitationDecision`、`ChatDecision`、`SpeechGeneration`、`SegmentSummary`、`ExitConsolidation`。
- 玩家邀请响应仍由玩家 API 决定；Agent 不代替玩家。
- 第三名 NPC 加入前的消息不会进入其 Agent State；玩家加入后仍可由公开 API 读取此前聊天记录。
- 结构化输出与模型不可用的现有简单兜底保持不变。

## 2. 文件与职责

新增 `core/backend/app/agents/`：

- `models.py`
  - `AgentEventType = daily_tick | invitation_received | chat_message_received`
  - 有类型的 `AgentGraphState`
  - `AgentInvocation`、`AgentResult`、`MemoryToolContext`、`MemoryToolResult`
  - 内部 `AgentTrace` 数据结构
- `memory_tool.py`
  - 正式只读工具 `RetrieveOwnedMemoriesTool`
  - 只接受模型可见的 `MemoryQuery`
  - 强制用运行时注入的 owner 和只读内存快照过滤、评分
- `trace.py`
  - `AgentTraceSink` 协议
  - 默认内存实现 `InMemoryAgentTraceSink`
- `runtime.py`
  - `NPCAgentRuntime`：构建一次并复用统一 `StateGraph`
  - `NPCAgent`：绑定一个 NPC 身份、工具权限和共享 Runtime
  - `NPCAgentRegistry`：为场景中的五名 NPC 构建五个逻辑 Agent

现有文件修改：

- `ai/protocols.py`：给 `MemoryQuery` 增加 `limit`，范围 `1..8`、默认 `8`；不增加 owner/run/conversation 字段。
- `orchestration/run_service.py`：只负责构建合法输入、触发对应 Agent、应用最终输出和执行世界状态变化；删除旧手写召回分支和 `_retrieve_memories`。
- `pyproject.toml`、`environment.yml`：增加 `langgraph>=1,<2`。
- `test/backend/`：新增 Agent Graph、工具隔离、追踪及接入回归测试。

## 3. Agent 调用契约

`NPCAgent` 对外只提供四个异步方法：

```text
daily_tick(invocation) -> AgentResult[DailyActionDecision]
invitation_received(invocation) -> AgentResult[InvitationDecision]
chat_message_received(invocation) -> AgentResult[ChatDecision]
generate_speech(prompt) -> SpeechGeneration
```

前三个方法进入共享编译 Graph；`generate_speech` 是胜出 Agent 自己的方法，但不增加第四种 Graph 入口，因为发言者已经由 Conversation 调度器确定，不需要再次进行条件编排。

每个 `NPCAgent` 在调用前强制检查 `invocation.npc_id == agent.actor_id`。Agent 不接收可写 `Run`，只接收：

- 后端生成的协议提示词或只读提示词构建器；
- 合法候选 ID；
- 当前 NPC 实际可见消息的深拷贝；
- 当前 NPC 的 Memory Cache ID 副本；
- 只读 Memory 快照与 Topic 映射；
- 由后端提供的 run/conversation/trigger 标识。

Agent 输出只包含结构化决定、召回到的 Memory ID、草稿语义副本和内部 traceId。它不能返回坐标、Conversation 操作、正式 Memory/Goal ID、权威时间或结算结果。

## 4. Graph State

`AgentGraphState` 至少包含：

```text
trace_id
run_id
conversation_id?
npc_id
event_type
trigger_message_id?
candidate_actor_ids
visible_messages
memory_cache
recall_used
prompt
prompt_builder?
memory_tool_context?
memory_tool?
decision?
draft_changes
recalled_memory_ids
final_output?
node_path
tool_used
tool_result_count
failure_code?
```

State 只存在于单次 `ainvoke` 内，不配置 checkpoint，不持久化，不进入公共 REST/WebSocket。`prompt_builder` 只读地根据“现有缓存 + 新召回 ID”生成第二次 `ChatDecision` 提示词；它不能写入 `Run`。

## 5. LangGraph 节点图

Graph 在 `NPCAgentRuntime.__init__` 中构建并 `compile()` 一次，五个 Agent 和所有请求复用同一编译结果。

```text
START
  -> route_event
       daily_tick ----------> daily_decision ----------> finalize
       invitation_received -> invitation_decision ----> finalize
       chat_message_received -> chat_decision
                                  | decided -----------> finalize
                                  | need_memory + unused
                                  v
                           retrieve_owned_memories
                                  | results -----------> chat_after_recall -> finalize
                                  | empty/error -------> safe_wait -------> finalize
                                  | second need_memory -> safe_wait -------> finalize
  -> END
```

所有业务节点使用 `async def`。每个节点只返回 State 的局部更新，并把自己的稳定节点名追加到 `node_path`。`safe_wait` 统一产生 `ChatDecision(result="decided", action="wait")`。

路由规则：

- `daily_tick` 只能产出 `DailyActionDecision`。
- `invitation_received` 只能产出 `InvitationDecision`。
- `chat_message_received` 先调用一次 `ChatDecision`。
- 只有第一次结果为 `need_memory` 且 `recall_used=False` 时进入工具节点。
- 工具空结果、工具异常、召回后的第二次 `need_memory` 都直接 `safe_wait`，不形成循环边。

## 6. 私有记忆工具

模型可见输入严格复用扩展后的 `MemoryQuery`：

```json
{
  "queryText": "string",
  "actorIds": ["actor id"],
  "topicHints": ["topic name/id/alias"],
  "goalIds": ["goal id"],
  "limit": 8
}
```

该 Schema 使用 `extra=forbid`。因此 `ownerNpcId`、`runId`、`conversationId` 既不在模型 Schema 中，伪造时也会被拒绝。

`MemoryToolContext` 由 Runtime 注入，包含：

- `owner_npc_id`
- `run_id`
- `conversation_id`
- 当前调用时的只读 Memory 深拷贝
- Topic ID/name/alias 的只读索引

工具执行顺序固定为：

1. 先过滤 `memory.ownerNpcId == context.owner_npc_id`。
2. 再计算 Actor、Goal、Topic、词项命中和重要度。
3. 按现有权重排序，最多返回 `query.limit` 条 Memory ID。
4. 返回结果不修改 Memory Cache 或任何世界状态；Graph 把 ID 放入自己的 State，`RunService` 在 Agent 完成后再合并到该 NPC 的会话缓存。

评分保持现有语义：Actor 命中 `*5`，Topic 命中 `*4`，Goal 命中 `*4`，词项命中 `*2`，其后以重要度和稳定 ID 排序。当前阶段不加入向量或数据库 Graph 查询。

## 7. RunService 接入

### 7.1 初始化

`RunService` 创建一个共享 `DecisionService`、一个 `NPCAgentRuntime` 和一个 `NPCAgentRegistry`。Registry 按场景 NPC 列表创建五个 `NPCAgent`；每个 Agent 绑定自己的 actorId 和独立工具权限对象，模型端口和编译 Graph 共享。

### 7.2 每日行动

`_run_daily_thought_locked` 保留候选过滤和 Prompt 构建，将合法候选、可见私有上下文副本交给 `agent.daily_tick`。Agent 返回后，`RunService` 继续验证 Goal 所有权和候选合法性，然后才移动和创建邀请。

### 7.3 邀请响应

`_request_invitation_locked` 为目标 NPC 构造 `invitation_received`。输入只含发起者 ID、可见请求、接收者自己的上下文与记忆，不含邀请记录中的 `_goalId`、`_intent` 或发起者私有数据。接受/拒绝后的事件顺序和 Conversation 创建仍由 `RunService` 执行。

### 7.4 聊天与发言

消息轮次编排器先为本轮全部合格 NPC 构造冻结的 `AgentInvocation`，再并行执行各自的 `agent.chat_message_received`：

- `visible_messages` 来自现有 `_visible_messages`；
- `memory_cache` 是 `(conversationId, npcId)` 的副本；
- 工具上下文使用 Memory 深拷贝并由 Runtime 注入 owner；
- `prompt_builder` 在召回后只读地重建 `chat_decision_with_memory` 提示词；
- 全部 Agent 返回后，`RunService` 按稳定参与者顺序合并 `recalled_memory_ids` 并调用现有 `_apply_chat_drafts`；Provider 返回快慢不能改变草稿应用顺序。

原 `_retrieve_memories` 和手写的“第一次决策 → 检索 → 第二次决策”代码删除，不保留双路径。

所有合法选择 `speak` 的 NPC 都并行调用自己的 `generate_speech(...)`，每个 NPC 每轮至多一句；不再存在 winner-only 路径。收集完毕后按“直接点名、responseDesire、加入顺序、actorId”稳定排序并逐条保存/广播。开场与 NPC 入场仍调用对应发起者/加入者自己的同一方法；玩家台词只来自真实玩家输入。

每个 Conversation 使用独立 worker/运行时锁，同一会话不重叠轮次，不同会话可以并行等待。所有实际模型请求继续共享 `DecisionService` 的物理请求 Semaphore；单 NPC 超时或失败只把该 NPC 降级为等待/无台词。

## 8. 内部追踪

每次前三类 Graph 调用生成 UUID `traceId`，并在完成或安全失败时写入内部 `AgentTraceSink`：

- `trace_id`
- `run_id`、`conversation_id`、`npc_id`
- `event_type`
- `node_path`
- `tool_used`
- `tool_result_count`
- `final_action`
- `duration_ms`
- `failure_code`

追踪不保存 Prompt、Memory 查询正文、Memory 内容、人设秘密、Goal 内容、关系数值或完整 Graph State。默认 Sink 只供进程内诊断和测试读取，不增加公共 API 或 WebSocket 事件。

安全失败码只保留少量稳定值：`model_fallback`、`tool_error`、`tool_empty`、`recall_limit`、`invalid_agent_binding`。不实现重试队列、熔断或复杂恢复。

## 9. 并行实施边界

- Agent A：只修改 `core/backend/app/agents/`、`ai/protocols.py`、依赖文件，完成 Graph/Tool/Trace 核心。
- Agent B：只修改 `orchestration/run_service.py`，按本设计接入 `NPCAgentRegistry`，移除旧召回路径。
- Agent C：只修改 `test/backend/` 和子智能体审查记录，新增本阶段测试并审查隐私边界。
- 主会话：处理接口冲突、修复生产代码与测试、更新验收文档、运行全部门禁和 Git 提交。

## 10. 验收映射

1. Registry 数量、actor 绑定、共享 `DecisionService`/compiled graph、各自工具权限与 cache key 隔离测试。
2. `daily_tick` trace 路径测试，并断言 Agent 调用期间位置不变、返回后由 `RunService` 移动。
3. `invitation_received` trace 与 Fake Model Prompt 测试，断言目标 Agent 决策且无 `_goalId`/`_intent` 私有值。
4. `chat_message_received` trace 包含 `chat_decision`；源码/行为测试证明不再调用旧 `_retrieve_memories`。
5. `MemoryQuery` 伪造 owner 字段校验失败；trace 路径进入 `retrieve_owned_memories`。
6. 第二次请求、空结果和工具异常分别只调用一次工具并安全返回 wait。
7. 高度相似的跨 owner Memory 隔离测试。
8. NPC 加入前消息不可见与玩家完整历史的现有测试继续通过，并补充 Agent State 断言。
9. 两个 Agent 同时 speak 时只胜出者调用自己的 `generate_speech`。
10. 现有草稿、离场沉淀、departed、三类 Day7 结局测试不变。
11. Fake Model/Tool/Trace 全部离线；断言节点路径、次数和失败码。
12. 全量 pytest、Ruff、mypy、应用导入和密钥扫描均通过，实际结果写入验收报告。

## 11. 完成门槛

- 没有任何公共响应或事件包含 Agent Trace、Prompt、Memory 查询、私有 Memory 或 Graph State。
- `RunService` 中不再存在 `_retrieve_memories` 或手写二次召回循环。
- Graph 编译发生在服务初始化，不在消息处理函数中构建。
- 不增加数据库、MCP、前端、Embedding、通用 ReAct Agent 或与当前玩法无关的 LangChain 模块。
- 当前 53 项测试与新增测试全部通过，静态检查和应用导入通过。
