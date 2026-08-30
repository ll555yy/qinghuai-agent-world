# 可直接创建 Goal 的提示词：消息驱动并行聊天轮次

```text
目标：严格按照 project/MESSAGE_DRIVEN_CHAT_ROUNDS_DESIGN.md 和 project/MESSAGE_DRIVEN_CHAT_ROUNDS_IMPLEMENTATION_PLAN.md，实现消息驱动的并行聊天轮次。保留现有 NPCAgent/LangGraph 记忆召回、NPC 私有可见性、conversation_drafts、ExitConsolidation、PostgreSQL 权威状态和 Day7 结算框架；完成同轮 NPC 并行判断、多人回复、稳定自然发布、玩家插话排队、一次冷场复询与正常离场，并完成与风险相称的全套验证和一个本地提交。

开始前必须完整阅读并遵守：
- project/MESSAGE_DRIVEN_CHAT_ROUNDS_DESIGN.md
- project/MESSAGE_DRIVEN_CHAT_ROUNDS_IMPLEMENTATION_PLAN.md
- project/SYSTEM_DESIGN.md
- project/DATABASE_BACKEND_DESIGN.md
- project/NPC_AGENT_LANGGRAPH_DESIGN.md
- project/FRONTEND_EVENT_MAPPING.md
- core/backend/app/orchestration/run_service.py
- core/backend/app/domain/run.py
- core/backend/app/ai/decision_service.py
- 相关 persistence、Conversation API、EventHub、前端 worldStore/ChatPanel 和聊天测试

工作树与 Git 规则：
1. 开始时检查 git status、diff、当前分支和现有测试。当前工作树可能已有用户授权但未提交的聊天、Toast、前端和人设修改；全部保留并人工整合，禁止 reset、checkout 丢弃文件、clean、覆盖或回退用户改动。
2. 若能在不丢失当前改动的情况下安全操作，创建并切换到 `codex/message-driven-chat-rounds`；如果分支已存在或切换会造成风险，留在当前分支并说明，不得为了建分支清理工作树。
3. 禁止 `git add .`。只暂存本 Goal 和当前已确认相关修改，创建一个本地提交；不 merge、不 push，除非用户随后明确要求。
4. 不修改或重新冻结历史 evaluation/simulation manifest、canonical、digest、seed、阈值和证据目录。若旧 digest 门禁因已授权的人设 YAML 改动失败，应准确报告，不得篡改历史产物来让测试变绿。

允许的并行方式：
1. 可以使用最多 3 个 Luna Max 子智能体并行完成相互独立的实现或验证工作。
2. 建议分工：
   - Luna A：轮次领域状态、codec、PostgreSQL projection/migration、恢复和持久化测试；
   - Luna B：前端事件/类型/逐条展示、玩家插话体验、Playwright 和前端测试；
   - Luna C：只读审计并发/stale/日终风险，或补独立并发屏障测试，默认不编辑核心编排文件；
   - 主智能体：独占 `run_service.py`，负责并行 ChatDecision、多人 SpeechGeneration、Provider 信号量、冷场状态机、提示词整合、跨模块审查、真实模型烟测和最终提交。
3. 所有智能体共享工作树。派发前先声明文件所有权，不允许两个智能体同时编辑 `run_service.py`、同一 migration 或同一测试文件。主智能体必须审查子智能体实际 diff，不能直接相信总结。
4. 子智能体不得提交、push、清理工作树、启动无界服务、修改冻结评估产物或调用真实 Provider。真实模型调用只能由主智能体有界执行。

必须实现的行为：
1. 消息驱动轮次：发起者先说话；NPC 加入者先说入场话；玩家发起或加入后由玩家第一条真实输入作为开场/入场话，系统不得替玩家编造内容。
2. 每批新消息触发当前在场、符合条件的全部 NPC 并行执行现有 NPCAgent ChatDecision。需要 Memory 时继续走 LangGraph 的 owner 私有检索和第二次决策，禁止复制、旁路或削弱检索流程。
3. 多个 NPC 都决定 speak 时，全部并行 SpeechGeneration；每 NPC 每轮最多一条。同轮输入使用冻结快照，不读取同轮尚未提交的其他台词。
4. 所有决定完成后统一校验并合并到各自 conversation_drafts。草稿可以持久化但不等于正式状态；Goal、关系、Memory 和章节状态仍只在 NPC 离场/会话关闭的 ExitConsolidation 中幂等提交。
5. 同一 Conversation 只能有一个活动轮次；不同 Conversation 的模型等待可以并行。用每会话运行时锁/任务状态消除当前 Run 级 chat_pipeline_lock 对两场聊天的串行效果，但共享状态应用仍用短暂 Run.lock 串行化。
6. 在物理 Provider 请求边界增加可配置全局 asyncio.Semaphore，默认 6。ChatDecision、记忆后的第二次决策、SpeechGeneration、SegmentSummary、ExitConsolidation 等真实物理请求都应受同一上限约束；等待网络、展示间隔时不得持有 Run.lock 或数据库事务。
7. 稳定发布顺序：直接点名/直接问题优先，其次 responseDesire 降序、加入顺序、actorId。并行生成后按该顺序逐条持久化并广播，默认使用由文本长度、roundId 和固定 seed 决定的 1.2～3.0 秒间隔；不能由 Provider 返回速度决定顺序，不能等整轮结束后一次广播全部消息。
8. 玩家消息先写入并立即显示。玩家在 deciding/generating/publishing 阶段发言时，消息进入 queuedMessageIds，当前轮继续，结束后将排队玩家消息和本轮 NPC 消息组成下一轮；不能启动重叠轮次，也不能仅因 latestMessageId 改变就误判当前结果 stale。玩家离开立即生效，不等待模型。
9. 一轮无人发言后进入默认 12 秒、可配置的 cooldown。等待期间玩家发言立即取消计时并完全重置冷场状态。到期后只并行执行一次 final_check；仍无人说话时，所有 NPC 走正常 leave/consolidate，玩家不被强制离开。
10. 删除固定约 4 秒 Conversation 自主续聊的职责，以及只选一名 speaker 和可见回复链上限。可以保留可配置的异常安全预算：连续 NPC-only 轮次达到预算时只进入冷场周期，不能作为正常对话的固定回复上限。
11. 保留参与者变化 Segment 边界、新加入 NPC 不读取旧消息、stale 结果丢弃、18:00/Day7 已授权调用收束、commandId 幂等和 PostgreSQL 重启恢复。
12. 单个 NPC 决策或台词超时/失败只影响该 NPC，不取消其他成功结果；整轮全部失败才进入冷场。近重复或只回应自己的台词应降级为 wait，避免机械无限接龙。

实现顺序与门禁：
1. 先完成持久化轮次状态、恢复语义和每会话唯一活动轮次测试。
2. 再完成并行 ChatDecision、统一草稿合并、全局物理请求信号量与并发屏障测试。
3. 再完成多人 SpeechGeneration、稳定逐条发布、WebSocket 事件和玩家插话队列。
4. 最后完成开场/加入、cooldown/final_check、旧 4 秒路径清理、提示词与前端体验。
5. 每阶段先跑相关测试，失败时修正根因，不用放宽断言掩盖竞态。

必须覆盖的测试：
- 同会话两 NPC ChatDecision 真实时间重叠；两场会话也能重叠；全局并发不超过配置值。
- 同轮两 NPC 都 speak 时都能生成和发布，顺序稳定，下一轮看到完整上一轮。
- 单项决定失败、单项 SpeechGeneration 失败、格式重试和超时不拖死整轮。
- 玩家在 deciding、generating、publishing、cooldown 四阶段发言均立即可见、只进入一次正确下一轮；玩家立即离开不等待。
- NPC/玩家发起，NPC/玩家加入，Segment 可见性和玩家不被自动代言。
- cooldown 被玩家发言取消；无玩家消息时只 final_check 一次；再次沉默后 NPC 离场并只沉淀一次。
- 并行决定只更新 conversation_drafts；正式 Goal/关系/章节状态离场前不变，离场后幂等提交。
- stale round、参与者变化、进程重启、重复 commandId、18:00/Day7 边界和 PostgreSQL revision 不丢写。
- WebSocket 按自然间隔逐条收到 message_created，前端无重复、无错序。

验证要求：
1. 运行新增和相关 pytest，然后运行后端完整 pytest、Ruff、mypy。
2. 运行前端 unit、lint、typecheck、build 和适用 Playwright。
3. 运行适用 PostgreSQL integration/migration 往返与重启恢复测试。
4. 用户已经允许本项目使用火山方舟模型。确定性测试通过后，由主智能体进行一组有界真实烟测：双 NPC 同轮回复、两会话并行、冷场离场；记录物理调用数、并发峰值、延迟、Token/成本（若 Provider 返回）、错误和限流。不得进行无界试玩或压力调用。
5. 执行 git diff --check、敏感凭据/私有 Prompt/绝对本机路径扫描，并核对设计、API、事件映射与实现一致。

完成前必须自行审查：是否仍有逐个 await NPC、winner-only、4 秒续聊、Run 级两会话串行、按 Provider 返回顺序应用草稿、整轮末尾批量广播、玩家插话令旧轮误 stale、提前提交正式状态等残留路径。发现任何一项都要继续修复和验证，不能提前结束。

最终汇报必须包含：最终状态机与旧实现差异；并发上限和实测重叠证据；开场/加入/多人回复/冷场行为；草稿与正式提交边界；测试逐项结果及已知基线失败；真实模型调用统计；改动文件；本地提交 hash；仍存在的风险。不要只汇报过程。
```
