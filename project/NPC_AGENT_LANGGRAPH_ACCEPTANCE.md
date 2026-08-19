# NPC Agent 化与 LangGraph 工具式记忆召回验收报告

状态：主会话验收通过

日期：2026-08-19

基线：迁移前 53 项测试通过；迁移后 73 项测试通过。

## 1. 实际交付

- 五名 NPC 已构造成五个长期复用的逻辑 `NPCAgent`，各自绑定 actorId 和 owner 专属记忆工具；共享一个 `DecisionService`、一个 Doubao TextModel 端口和一个编译后的 LangGraph。
- LangGraph 1.2.11 自定义 `StateGraph` 提供 `daily_tick`、`invitation_received`、`chat_message_received` 三个入口。
- 每日行动、邀请响应和聊天决定都经过 Agent Graph；开场台词及调度胜出者台词由对应 NPC Agent 的 `generate_speech` 生成。
- 长期记忆召回已成为显式只读工具节点 `retrieve_owned_memories`。模型 Schema 只暴露 `queryText`、`actorIds`、`topicHints`、`goalIds`、`limit`。
- `ownerNpcId`、runId、conversationId 由 Runtime 注入。其他 NPC 的私有 Memory 在进入 Graph State 前已被过滤，工具内部仍重复校验 owner。
- 同一 NPC/同一触发消息跨 Graph 调用也最多召回一次；空结果、异常和第二次请求都安全转为 `wait`，没有循环边。
- `RunService` 中旧 `_retrieve_memories` 和手写二次决策分支已删除；移动、邀请事件、Conversation、发言调度、草稿提交、departed 和 Day7 结算仍由世界层掌控。
- 每次 Graph 执行产生内部 traceId 和最小诊断轨迹，不保存 Prompt/Memory 内容，也不进入公共 REST/WebSocket。

## 2. 主会话独立审查修正

并行实现合并后，主会话没有直接接受子智能体结论，而是重新审计 Graph State、RunService 接口和隐私投影，并修正：

1. 将 Memory 工具上下文从“整库深拷贝后由工具过滤”改为“进入 Graph State 前先按 owner 最小权限过滤，工具执行时再二次校验”。这保证 npc_001 的 Graph State 本身也不会携带 npc_002 的私有 Memory。
2. 过滤传入 Agent State 的 Memory Cache ID，阻止异常缓存把其他 owner 的 ID 带入私有上下文。
3. 增加同一 run/conversation/triggerMessage 的跨调用召回记录，避免同一消息因重复编排再次调用工具。
4. 把 `RunService._npc_agent` 从 `Any` 改为明确 `NPCAgent`，并在三个协议入口对联合决定做显式类型收窄。
5. 增加真实 RunService 邀请隐私测试、Agent 无法修改坐标测试、Graph State owner 快照测试和 REST 集成 trace 隔离测试。

## 3. 十二项验收映射

1. **五 Agent 与共享模型**：通过。Registry 构造五个不同对象和五个 owner 绑定工具，Runtime、DecisionService 和 compiled graph 相同。
2. **daily_tick 与世界权威**：通过。trace 路径为 `route_event → daily_decision → finalize`；Agent 决策测试期间 Run 坐标不变，移动仍由 RunService 执行。
3. **invitation_received 隐私**：通过。真实 RunService 测试确认接收方 Prompt 没有发起者私有 goal/intent 标记。
4. **聊天流程单一路径**：通过。`chat_message_received` 进入 Graph；RunService 不再存在 `_retrieve_memories` 或手写 `need_memory` 分支。
5. **正式工具与 owner 注入**：通过。`MemoryQuery` 伪造 `ownerNpcId` 被 Pydantic `extra=forbid` 拒绝；trace 可见工具节点。
6. **单次召回与失败收束**：通过。覆盖同一次 Graph 二次请求、同消息跨 Graph 重入、空结果和工具异常，工具次数均不超过一次。
7. **跨 owner 隔离**：通过。相同文本的外部 owner Memory 不会出现在工具结果；RunService 提供的 Graph State 快照只含当前 owner。
8. **第三人消息可见性**：通过。既有回归测试确认第三 NPC 看不到加入前消息，玩家加入后仍可看到历史；Agent State 使用该 NPC 的 `_visible_messages` 深拷贝。
9. **唯一发言者**：通过。两个 Agent 同时申请发言时，只有调度胜出 Agent 的 `generate_speech` 被调用。
10. **玩法状态兼容**：通过。原有 Goal/关系/立场草稿、离场沉淀、departed 与 Day7 三分支测试全部保持通过。
11. **离线 Fake 与 trace**：通过。Fake TextModel、Fake Memory Tool、内存 Trace Sink 不访问网络，并断言节点路径、工具次数和失败码。
12. **全量质量门禁**：通过。pytest、Ruff、mypy、应用导入和密钥扫描结果见下节。

## 4. 实际门禁结果

使用 `E:\anaconda3\envs\qinghuai-chat\python.exe`，并将仓库根目录加入 `PYTHONPATH`：

```text
pytest -q
73 passed, 1 warning in 5.46s

ruff check core/backend/app test/backend
All checks passed!

mypy --config-file core/backend/pyproject.toml core/backend/app
Success: no issues found in 46 source files

from core.backend.app.main import create_app
应用导入通过

密钥模式扫描
未发现 sk-/ark- 形式密钥或源码中的真实 API Key 赋值
```

唯一 warning 是既有 FastAPI TestClient 对 Starlette/httpx 组合的弃用提示，不是本阶段引入的失败。

## 5. 并行子智能体记录

- Luna A：实现 Agent/StateGraph/Tool/Trace 核心与依赖。
- Luna B：迁移 RunService 三入口、发言归属和旧召回路径。
- Luna C：新增离线测试并形成独立审查记录。
- 主会话：定稿架构、整合接口、修复隐私与跨调用召回问题、补集成测试并执行最终门禁。

子智能体的阶段性结果与早期问题保存在 `NPC_AGENT_LANGGRAPH_SUBAGENT_REVIEW.md`；最终结论以本报告的主会话复验为准。

## 6. 明确延后

- PostgreSQL、pgvector、数据库 Graph、Embedding 与 checkpoint。
- MCP Server 或外部工具协议适配。
- React/Phaser 前端和 Workflow 可视化。
- 多实例并发、Redis/消息队列、复杂重试、熔断和多模型复核。

这些延后项没有伪实现或空壳依赖；当前闭环使用内存 Run、内存原子 Memory 和正式内部 Agent Tool。
