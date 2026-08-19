# 下一阶段 Goal：NPC Agent 化与 LangGraph 工具式记忆召回

以下正文供用户复制后自行设置 Goal；本文件不代表已经创建或启动 Goal。

```text
继续开发 C:\Users\yangruiqi\Desktop\chat 中的“青槐老巷聊天世界”。

目标：在不重写现有世界引擎、不接数据库和前端的前提下，把五名 NPC 改造成具有独立私有上下文和工具集合的逻辑 Agent；使用 LangGraph 明确编排 Agent 的每日行动、邀请响应和聊天消息处理；把长期记忆召回实现为由 Agent 决策、LangGraph 工具节点执行的只读工具调用。

开始前完整阅读并遵守：
- project/PROJECT_DESIGN.md
- project/PROJECT_RULES.md
- project/SYSTEM_DESIGN.md
- project/BACKEND_PLAYABLE_LOOP_DESIGN.md
- project/BACKEND_PLAYABLE_LOOP_ACCEPTANCE.md
- project/BACKEND_PLAYABLE_LOOP_SUBAGENT_REVIEW.md
- core/scenario/ 下的运行配置

先检查当前 Git 工作树，保留用户已有修改。完整理解现有 RunService、DecisionService、六类 Pydantic AI 协议、Memory owner 隔离和 53 项测试后再设计。先把本阶段设计、状态边界、LangGraph 节点图和验收标准落盘到 project/，再开始修改代码。

必须实现：

1. NPC Agent 化
- 新增清晰的 NPCAgent 或 NPCAgentRuntime 抽象。每个 NPC Agent 由 actorId、私有人设、自己的 Goal、自己指向他人的关系、私有 Memory 范围、可用工具和工作流共同构成。
- 五个 NPC 是五个逻辑 Agent，但继续共用现有 Doubao-Seed-2.0-lite TextModel，不创建五个模型客户端或五个常驻进程。
- Agent 只能提出语义决定或工具请求，不能直接修改 Run、坐标、Conversation、Goal、关系、Memory 或章节结算状态。
- Run 和 RunService 继续是世界权威状态；Conversation 调度器继续负责从多个发言申请者中选择唯一发言者。

2. LangGraph 工作流
- 增加兼容 Python 3.12 的 LangGraph 1.x 依赖，并使用 StateGraph 构建自定义、有类型的状态图；不要用一个不可解释的通用 ReAct Agent 替代现有明确协议。
- 为 NPC Agent 提供三个明确入口事件：daily_tick、invitation_received、chat_message_received。
- 可以使用一个带入口路由的统一 StateGraph，也可以使用共享状态模型的三个编译子图；必须避免复制三套上下文构建和校验逻辑。
- 图应在服务初始化时编译并复用，不能每条消息重新构建或编译。
- Graph State 至少包含：runId、conversationId（可选）、npcId、事件类型、triggerMessageId（可选）、候选角色、实际可见消息、Memory Cache、是否已经召回、结构化决策、草稿变化和最终 Agent 输出。
- 节点使用 async 实现，与现有异步 FastAPI/TextModel 调用兼容。

3. 每日自主行动由 Agent 决定
- 世界引擎仍按现有规则每天错峰触发每名 Agent 一次。
- daily_tick 进入 Agent Graph。Agent 根据人设、有效 Goal、关系、当天事件、已有私有记忆和后端提供的合法候选，输出现有 DailyActionDecision：seek_chat 或 wait。
- Agent 只决定找谁聊天及私有 intent。RunService 校验决定后执行坐标移动、movement 事件和邀请创建。
- 不让模型生成逐步移动路径，不让 Agent 直接写坐标。

4. 邀请接受或拒绝由目标 Agent 决定
- invitation_received 进入被邀请 NPC 自己的 Agent Graph，输出现有 InvitationDecision：accept 或 refuse。
- 目标 Agent 的输入不得包含发起者未说出口的 goalId、intent、秘密或私有 Memory。
- RunService 继续负责请求气泡清除、接受/拒绝事件顺序和 Conversation 创建。
- 玩家收到邀请仍由玩家 API 决定；不要让 Agent 代替玩家。
- NPC 发起的邀请被接受后，仍由发起 NPC 自己的 Agent 执行 SpeechGeneration 生成开场台词。

5. 聊天与发言仍属于对应 Agent
- chat_message_received 进入每个实际在场且有权看到该消息的 NPC Agent。
- Agent 继续使用现有 ChatDecision，决定 need_memory 或 speak/wait/leave_chat，并可同时产生合法的 Goal、关系、待创建短期 Goal 和章节立场草稿。
- Conversation 调度器收集各 Agent 的发言申请并选出唯一发言者；选中后调用该 NPC 自己的 Agent 执行 SpeechGeneration。调度器不生成台词。
- 未获发言权 Agent 的合法草稿仍立即影响该 Agent 在同场后续提示词，并在离场时一次提交。
- SegmentSummary、ExitConsolidation、Day7 固定结算的语义与现有实现保持一致。

6. 工具式私有记忆召回
- 把现有 _retrieve_memories 封装为正式的只读 Agent Tool，例如 retrieve_owned_memories，并通过 LangGraph 的工具节点或等价的显式工具执行节点调用。
- 工具的模型可见输入只允许 queryText、actorIds、topicHints、goalIds 和 limit；ownerNpcId、runId、conversationId 必须由 Agent Runtime/LangGraph State 或 ToolRuntime 注入，不能由模型提供或覆盖。
- 工具先强制 ownerNpcId 过滤，再使用当前内存 Graph 的 Actor/Goal/Topic/词项/重要度排序，返回当前 NPC 自己的原子 Memory。当前阶段不接 PostgreSQL、pgvector或 Embedding。
- 同一个 NPC Agent 对同一条触发消息最多调用一次新增记忆召回。召回结果进入本场该 Agent 的 Memory Cache；已有缓存足够时直接复用。
- 第二次仍请求记忆、工具失败或没有匹配结果时安全转为 wait/不知道/不确定，不能循环调用、读取其他 NPC Memory 或编造记忆。
- Tool 本身不修改 Goal、关系、Memory、Conversation 和世界状态。

7. 现有协议与代码迁移
- 继续使用 DailyActionDecision、InvitationDecision、ChatDecision、SpeechGeneration、SegmentSummary、ExitConsolidation 六类协议，不新增一个包揽所有职责的大型输出 Schema。
- 将 RunService 中手写的“决策 → need_memory → 检索 → 再决策”聊天分支迁入 LangGraph，避免新旧两套流程并存。
- RunService 只负责触发 Agent Graph、校验最终 Agent 输出、执行世界状态变化和发布公共事件。
- DecisionService 和 ArkClient 继续作为模型适配层；LangGraph 节点不得直接依赖火山方舟 SDK。
- 保留现有简单兜底：结构化输出必要时最多重试一次，缺少模型配置时 daily=wait、invitation=refuse、chat=wait、speech 不生成；不要增加熔断、多模型复核、复杂恢复或多级缓存。

8. 可观测但不泄密
- 为每次 Agent Graph 执行生成内部 traceId，记录入口事件、经过的节点、是否调用工具、工具结果数量、最终动作、耗时和安全失败码。
- 公共 REST/WebSocket 只能按现有规则公开玩家可见事件，不得公开提示词、人设秘密、深层 Goal、私有 Memory、关系数值、Memory 查询内容、工具原始结果、Graph State 或 LangGraph 内部轨迹。
- 测试可以通过内部接口或 Fake Trace Sink 检查执行路径，不能为了测试把私有轨迹加入公共 API。

明确不做：
- 不接 PostgreSQL、pgvector、Neo4j、Redis、数据库 checkpoint 或 Embedding。
- 不实现 MCP Server；本阶段先完成内部 Agent Tool，后续可以在保持同一工具契约的前提下增加 MCP 适配器。
- 不创建 React/Phaser 前端或可视化 Workflow 编辑器。
- 不修改 Day1-Day7 剧情、NPC 人设、Goal、关系、章节立场规则和 Day7 结算阈值。
- 不把世界时间、移动、Conversation 约束或结算权威交给 LangGraph/模型。
- 不为了简历关键词引入 LangChain 其他无实际用途的模块。

验收测试至少证明：
1. 五名 NPC 在运行时被构造成五个独立逻辑 Agent，共享模型端口但私有上下文、Memory Cache 和工具权限相互隔离。
2. daily_tick 确实经过 LangGraph 节点并产生 seek_chat/wait；移动和邀请仍由 RunService 执行，Agent 无法直接修改坐标。
3. invitation_received 确实由目标 NPC Agent 判断 accept/refuse，提示词不含发起者私有 goalId/intent。
4. chat_message_received 的现有决策流程已由 LangGraph 执行，不再同时走旧的手写召回分支。
5. need_memory 会进入正式记忆工具节点，ownerNpcId 由运行时注入；伪造 owner 字段被 Schema 拒绝或完全不可见。
6. 同一 NPC/触发消息最多召回一次；第二次请求、空结果和工具异常均安全结束，不发生循环。
7. npc_001 的工具结果和 Graph State 中永远不会出现 npc_002 所有的私有 Memory，即使两条 Memory 文本高度相关。
8. 第三 NPC 加入前的聊天记录不会进入其 Agent State 或记忆证据；玩家加入后仍能看到此前聊天记录。
9. 多个 Agent 同时申请发言时仍由 Conversation 调度器选一个；胜出 Agent 自己生成台词，其他 Agent 不调用 SpeechGeneration。
10. Agent 的 Goal/关系/立场草稿、离场沉淀、departed 和 Day7 三种结局与迁移前行为一致。
11. Fake TextModel/Fake Tool/Fake Trace 测试不访问真实网络，并能断言 LangGraph 的节点路径、工具次数和安全回退。
12. 现有全部测试继续通过，并新增 LangGraph 单元测试和 API 集成测试；pytest、Ruff、mypy、应用导入和密钥扫描全部通过。

实施原则：
- 这是一次边界清晰的架构迁移，不是整套平台重写。
- 第一版先让 Agent、LangGraph 和记忆 Tool 形成真实、可运行、可测试的闭环。
- 正常输入错误、状态冲突、模型失败和工具失败需要简单兜底；不为千分之一概率增加复杂机制。
- 不覆盖用户已有修改，不写入或打印任何真实密钥。
- 设计确认和实现结果继续落盘到 project/；运行代码只读取 core/ 下的配置。

协作方式：主会话负责迁移设计、状态边界和最终验收；生成一个 Luna Max 子智能体按设计实现并自审；主会话随后独立检查代码、修复问题，运行全部测试、Ruff、mypy、应用导入与密钥扫描，并将实际结果写入验收报告。子智能体若因额度或其他原因中断，必须如实记录，由主会话接管，不能把未完成自审声明为通过。

完成条件：实现、测试、设计、子智能体审查记录和主会话验收全部落盘；Git 工作树状态清楚；最终向用户报告实际测试结果、关键文件、已知延后项和本地提交信息。不要替用户创建或启动新的 Goal，用户会自行设置。
```

## 技术依据

本阶段使用自定义 `StateGraph`，因为当前流程有明确的协议、条件路由和世界状态边界，不适合直接替换为通用 ReAct Agent。LangGraph 官方参考说明 `StateGraph` 节点通过共享 State 读写局部更新，编译后可异步执行；`ToolNode` 用于需要细粒度控制的自定义工具工作流，并支持从运行时注入不暴露给模型的状态。
