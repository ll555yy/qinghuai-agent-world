# NPC Agent + LangGraph Luna Max 测试审查记录

- 阶段：NPC Agent 化与 LangGraph 工具式记忆召回
- 日期：2026-08-19
- 负责范围：新增 `test/backend/unit/test_npc_agent_components.py`，仅做离线契约测试与独立审查
- 结论：新增测试和现有 backend 测试通过；生产侧导入阻塞已由主会话修复，仍由主会话负责最终静态门禁和 Git 提交

## 新增测试覆盖

`test_npc_agent_components.py` 使用 Fake TextModel、Fake Memory Tool 和内存 Trace Sink，不访问网络，覆盖：

1. `MemoryQuery` 只暴露 `queryText`、`actorIds`、`topicHints`、`goalIds`、`limit`；伪造 `ownerNpcId` 和非法 limit 被拒绝。
2. 五个逻辑 Agent 共享同一个 Runtime、DecisionService 和 compiled graph，同时拥有不同 owner 绑定的私有工具。
3. `daily_tick`、`invitation_received`、`chat_message_received` 三个入口的路由、节点路径和内部 trace。
4. daily 决策只返回语义决定，不包含位置或 Conversation 权威字段。
5. invitation 提示上下文不包含发起者私有 `_goalId`、`_intent` 或秘密字段。
6. 同一 NPC/触发消息最多执行一次记忆工具；第二次 `need_memory` 转为 `wait` 并标记 `recall_limit`。
7. 工具空结果和异常各执行一次后安全 `wait`，不循环。
8. 高相似度跨 owner Memory 不会被召回，工具不修改输入快照。
9. Agent invocation 的可见消息和 Memory context 使用深拷贝。
10. Conversation 调度器只给最终胜出 Agent 调用 `generate_speech`。
11. 公共 Run/事件投影不包含 trace、Graph State、memoryQuery 等内部字段。

## 执行结果

先后执行：

```text
E:\anaconda3\envs\qinghuai-chat\python.exe -m pytest test\backend\unit\test_npc_agent_components.py --confcutdir=test\backend\unit -q
```

实现落盘后，第一次干净收集曾被阻塞：`core/backend/app/agents/runtime.py` 当时尚未落盘；随后发现 `trace.py` 从 `collections.abc` 导入 `Protocol`，Python 3.12 下直接 `ImportError`。该生产问题已报告给主会话并由主会话修复，本审查没有修改生产代码。修复后重新执行了无补丁命令：

```text
E:\anaconda3\envs\qinghuai-chat\python.exe -m ruff check test\backend\unit\test_npc_agent_components.py
All checks passed!

E:\anaconda3\envs\qinghuai-chat\python.exe -m pytest test\backend\unit\test_npc_agent_components.py -q
15 passed, 1 warning

E:\anaconda3\envs\qinghuai-chat\python.exe -m pytest test\backend -q
68 passed, 1 warning
```

当前测试曾暴露二次 `need_memory` 失败码为 `tool_empty`，实现已修正为 `recall_limit`，随后相关测试通过。pytest 的唯一输出是既有 FastAPI/httpx deprecation warning，不影响测试结果。

## 独立审查结论

- Agent State 是单次调用内的快照，测试确认消息与 Memory context 的调用方后续修改不会回写 State。
- `RetrieveOwnedMemoriesTool` 的 owner 来自运行时上下文和 Agent binding，模型查询 Schema 不含 owner 字段；测试确认相似的其他 NPC Memory 被过滤。
- 三类 Graph 入口和工具节点路径可通过内部 Trace Sink 观察，公共 Run/事件输出没有新增内部轨迹。
- SpeechGeneration 仍由 Conversation scheduler 选中的 Agent 执行，未获胜 Agent 不生成台词。
- 测试没有覆盖数据库、MCP、Embedding、前端或真实模型网络，符合本阶段范围。

## 待主会话处理

1. 已修复 `core/backend/app/agents/trace.py` 的 `Protocol` 导入；主会话继续运行全项目 Ruff、mypy、应用导入和密钥扫描。
2. 确认 RunService 初始化使用一个共享 Runtime/DecisionService，旧 `_retrieve_memories` 分支已删除且全部既有玩法测试保持通过。
3. 将上述 backend 测试结果合并进主会话验收报告。

本阶段未提交 Git；由主会话统一审查、修复并提交。
