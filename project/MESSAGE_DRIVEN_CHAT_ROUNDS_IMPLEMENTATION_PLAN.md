# 消息驱动并行聊天轮次实施计划

- 状态：已实施并完成本地验收；PostgreSQL 实库门禁因本机 Docker daemon 不可用而准确保留为环境跳过
- 依赖设计：`project/MESSAGE_DRIVEN_CHAT_ROUNDS_DESIGN.md`
- 实施原则：保留现有脏工作树和草稿/沉淀框架；先建立确定性轮次模型，再接并发和自然发布，最后删除旧 4 秒续聊路径

## 1. 完成定义

完成后，聊天满足以下端到端行为：

- 发起者和加入者先说话；玩家台词只能来自玩家输入。
- 同轮所有符合条件的 NPC 并行判断，所有决定发言者均可生成台词。
- 同轮台词并行生成、稳定排序、按自然间隔逐条持久化和广播。
- 同一 Conversation 不重叠轮次，不同 Conversation 可并行。
- 活动轮次中的玩家消息立即显示并进入下一轮，不使当前轮错误失效。
- 一轮无人回复后等待一次、复询一次，仍沉默则 NPC 离场。
- 会话草稿与 `ExitConsolidation` 的正式提交边界保持不变。

## 2. 阶段 0：基线与安全边界

1. 检查 `git status`、当前 diff、分支和已有测试；禁止清理或覆盖用户改动。
2. 阅读并锁定以下现有实现：
   - `core/backend/app/orchestration/run_service.py`
   - `core/backend/app/domain/run.py`
   - `core/backend/app/ai/decision_service.py`
   - `core/backend/app/agents/` 与模型 Provider 适配器
   - `core/backend/app/persistence/codec.py`
   - `core/backend/app/persistence/sqlalchemy_repository.py`
   - `core/backend/app/persistence/normalized_projection.py`
   - Conversation API、事件中心、前端 store/ChatPanel 及相关测试
3. 先运行聊天相关单测并记录已知基线失败。冻结的历史评估 manifest/digest 不得因本 Goal 顺手重写。

## 3. 阶段 1：轮次领域状态和持久化

1. 为每个 Conversation 增加持久化轮次状态：轮次 ID、状态、版本、触发消息、排队消息、冷场期限、最后复询标记和恢复信息。
2. 为运行时增加每会话锁/任务注册表；锁对象本身不序列化，进程恢复后按持久化状态重建。
3. 保持 `Run.lock` 为共享聚合状态的短临界区；移除 Run 级 `chat_pipeline_lock` 对两场聊天造成的串行效果。
4. 扩展 codec、PostgreSQL normalized projection、SQLAlchemy 模型和必要 migration。若审查证明现有通用状态项足以可靠承载轮次状态，可不新建独立表，但必须保证重启恢复和查询测试。
5. 增加 round/event/message 的幂等键与 stale 校验；排队中的玩家消息不能仅因 `latestMessageId` 变化而错误取消当前轮。

阶段门禁：编解码往返、PostgreSQL 保存/恢复、同会话唯一活动轮次、不同会话独立状态测试通过。

## 4. 阶段 2：并行 ChatDecision

1. 把当前逐个 `await _run_one_chat_decision_locked` 改为：持锁冻结快照、释放锁、使用 `asyncio.gather(..., return_exceptions=True)` 或等价 TaskGroup 并行执行、重新持锁统一校验。
2. 每个 NPC 仍调用现有 `NPCAgent.chat_message_received`；不复制或旁路 LangGraph 记忆召回。
3. 按 NPC 私有可见性分别构造输入；同轮输入不可被其他并行返回污染。
4. 等所有决定完成后，按稳定 actor 顺序统一调用现有草稿校验/合并逻辑。
5. 单项超时、结构失败和取消按该 NPC `wait`，不得取消同轮其他有效结果。
6. 在 Provider 物理请求边界增加可配置全局 `asyncio.Semaphore`，默认 6，并保证二次记忆决策也计入并发额度。

阶段门禁：屏障测试证明同会话 NPC 请求和两会话请求都真实重叠；并发上限、局部失败、私有 Memory owner 隔离测试通过。

## 5. 阶段 3：多人 SpeechGeneration 与草稿原子合并

1. 删除“从候选者中只选一个 winner”的普通轮次路径；保留排序评分作为稳定发布顺序的一部分。
2. 对所有合法 `speak` 决定并行生成台词；每 NPC 每轮至多一次。
3. 统一验证非空、角色仍在场、Segment/round 版本、重复文本和 spoken chapter effect 证据。
4. 只有 SpeechGeneration 成功且实际发布的 NPC，才能把空 `evidenceMessageIds` 的 spoken chapter effect 绑定到新消息。
5. 同轮决定统一合并到 `conversation_drafts`；正式 Goal、关系、章节状态仍不提前提交。

阶段门禁：两 NPC 同轮发言、单人生成失败、同轮草稿确定性、spoken effect 证据绑定与离场只提交一次测试通过。

## 6. 阶段 4：稳定发布、事件与玩家插话

1. 按“直接点名、responseDesire、加入顺序、actorId”稳定排序。
2. 实现 1.2～3.0 秒的确定性展示间隔；间隔期间不持锁、不持事务、不占 Provider 并发名额。
3. 每条 NPC 消息分别执行：重新校验、写入、保存、广播 `message_created`；不能等整轮结束后一次广播。
4. 玩家消息仍乐观显示并立即持久化；活动轮次期间写入 `queuedMessageIds`，当前轮完成后只触发一次下一轮。
5. 玩家立即离开时取消其待处理入口并改变参与者版本；任何不再合法的旧结果必须丢弃。
6. 必要时扩展 API/前端类型，加入 `roundId`、`roundSequence`、`replyToMessageIds`；保持旧快照恢复兼容。

阶段门禁：WebSocket 逐条事件顺序、前端逐条展示、玩家 deciding/generating/publishing 三阶段插话、立即离开和重复 commandId 测试通过。

## 7. 阶段 5：开场、加入与冷场状态机

1. NPC 发起会话后强制发起者先生成开场白；其他 NPC 从开场消息进入普通轮。
2. 玩家发起时进入 `awaiting_player_opener`，第一条玩家消息启动普通轮。
3. NPC 加入后先说入场台词；玩家加入后等待玩家第一句话，不自动生成玩家台词，也不先让 NPC 欢迎。
4. 一轮无人发言进入默认 12 秒 cooldown；玩家新消息原子取消计时并重置。
5. cooldown 到期只执行一次 `final_check`；仍无人发言时强制 NPC 走正常离场/沉淀路径。
6. 删除 Conversation 固定约 4 秒自主唤醒和旧回复突发上限的业务语义；保留安全预算作为调度让步与异常防护，不作为可见对话上限。
7. 保留 18:00/Day7 在途调用屏障，并补多轮并发下的收束测试。

阶段门禁：NPC/玩家发起、NPC/玩家加入、冷场重置、只复询一次、强制离场、Segment 可见性和日终边界测试通过。

## 8. 阶段 6：提示词与前端体验

1. 更新 ChatDecision 规则：明确 `normal_round`、`join_opener`、`final_check`；有内容时积极承接，无新价值时等待，禁止机械重复。
2. SpeechGeneration 获得本轮目标消息 ID 和综合意图，但不能看到同轮未发布台词。
3. 前端显示可选的“正在思考/正在输入”，但不能暴露具体哪个 NPC 的私有决定或 Memory。
4. 保持 Enter 发送、Shift+Enter 换行、发送即显示和离开即返回；自然间隔由服务端事件节奏驱动。
5. 更新 OpenAPI 生成类型、设计文档和事件映射。

## 9. 阶段 7：回归、实测与收尾

按风险从低到高执行：

1. 新增聊天轮次单元测试和并发屏障测试。
2. 后端聊天、Segment、草稿、沉淀、日终相关测试。
3. 后端完整 pytest（历史冻结 digest 若因已有授权场景改动失败，必须单独说明，不能篡改历史 manifest）。
4. Ruff、mypy。
5. 前端 unit、lint、typecheck、build。
6. Playwright：玩家发言立即出现、多人逐条回复、冷场取消、立即离开。
7. PostgreSQL integration：并发两会话、重启恢复、幂等与 revision。
8. 在用户已授权的火山方舟范围内做一组有界真实模型烟测：一场双 NPC 回复、一组并行双会话、一组冷场离场；记录调用数、延迟、错误与是否限流。
9. `git diff --check`、敏感信息扫描和文档一致性检查。

## 10. 推荐的并行实施拆分

实现时最多使用 3 个 Luna Max 子智能体；它们共享工作树，因此文件所有权必须先划分，避免同时编辑 `run_service.py`：

- Luna A：领域轮次状态、codec、PostgreSQL projection/migration 和持久化测试。
- Luna B：前端事件/类型/展示队列、Playwright 与前端测试。
- Luna C：只读审计现有编排与测试缺口，或在主智能体完成核心编排后补独立并发测试；默认不编辑 `run_service.py`。
- 主智能体：独占 `run_service.py`、决策提示词、Provider 信号量整合、跨分支集成、真实模型烟测和最终验证。

子智能体不得提交、push、清理工作树、修改冻结评估产物或进行真实 Provider 调用。主智能体必须审查所有共享工作树变化后再运行整套验证。

## 11. 停止条件与交付

- 不以“主要代码已写”作为完成；必须达到第 1 节的端到端完成定义并通过相应验证。
- 若数据库迁移、事件逐条发布或重启恢复需要超出本方案的大框架改造，应停止扩张，记录证据并选择与现有 Repository/EventHub 最小兼容的实现。
- 最终交付包含：实现摘要、关键状态机差异、测试结果、真实模型调用统计、已知边界、改动文件和本地提交 hash。
- 默认只创建本地提交，不 push；若用户另行要求远端操作，再单独执行。

## 12. 2026-08-26 实施与验收记录

- 持久化：复用 PostgreSQL `run_state_items` 的通用权威状态项保存每会话轮次状态，不新增专用表或 migration；codec、SQLAlchemy 映射及服务重启恢复测试已覆盖。
- 后端相关测试：消息轮次文件 `13 passed`；完整 pytest 为 `285 passed, 11 skipped, 1 failed`。唯一失败是已有授权 `NPC_PERSONAS.yaml` 修改导致历史冻结场景 SHA-256 不匹配；按约束未重写历史 manifest。11 项 PostgreSQL 测试因本机 Docker daemon 不存在、未配置 `QINGHUAI_TEST_DATABASE_URL` 而跳过。
- 静态门禁：本次涉及的应用、脚本、迁移与测试文件 Ruff 全绿；mypy 为 `Success: no issues found in 79 source files`；应用导入通过。全仓 Ruff 仍报告 21 个未改动旧测试文件的既存 `I001` 导入排序问题，本次未扩大范围修改。
- 前端：Vitest `14 files / 59 tests`、lint、typecheck、production build 和 bundle-size gate 全绿；Playwright full-stack E2E `11 passed`。Phaser chunk 为 `1197.2 kB / 1200 kB`，接近但未超过既有门限。
- 真实方舟冒烟：报告见 `project/evaluation-results/message-driven-chat-rounds-real-smoke-2026-08-26.json`。双 NPC 同轮、两会话 ChatDecision 时间重叠、冷场 final check 与 NPC 正常离场均通过；峰值并发 `5`（配置上限 `6`），`16` 次逻辑调用、`17` 次物理尝试、`1` 次内部重试、`0` 个终态错误、无 rate limit，延迟 `52069 ms`，Token 为 prompt `54809`、completion `12249`、total `67058`。Provider 未返回账单金额，因此不伪造成本。
- 安全与收尾：真实报告只含计数和门禁，不含提示词、私有 Memory、生成台词、密钥或本机绝对路径；历史冻结证据未修改。
- 剩余部署边界：每会话 worker/lock 与 Provider semaphore 是进程内协调机制。当前单体部署满足约束；若未来让多个后端进程同时接管同一 Run，需要在 Repository 层增加跨进程 lease/队列所有权，不能仅依赖本次进程内锁。
