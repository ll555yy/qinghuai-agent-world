# 真实 AI、Embedding 与七日模拟实施报告

状态：真实文本、真实 Embedding、定向回填、真实 Day1 与三条真实七日路线全部通过。

## 已实现

- 方舟文本模型的每日行动、邀请、聊天决策、台词、分段摘要和离场沉淀六类协议已接入 Agent 决策链；所有结果使用严格 JSON 对象解析，格式失败至多重试一次。
- 增加显式 `--live` 的六协议验收工具。默认只检查配置和协议，不联网；报告不保存 Prompt、回复正文、密钥或 NPC 私密信息。
- 增加方舟 Agent Plan Embeddings 适配器、2048 维数据库迁移、新 Memory 事务外索引、幂等批量回填、内容变化失效和保存时向量保留机制。真实探测解析模型为 `doubao-embedding-vision-251215`，返回 2048 维。完整 float32 向量保存在 `vector(2048)`，HNSW 使用 `halfvec(2048)` 表达式索引。
- 混合召回继续以 `run_id + owner_npc_id` 作为硬隔离边界，并统计向量命中与 Graph 命中。
- 25 条有向初始关系全部拥有 NPC 私有 `scenario_seed` Memory；关系数值仍是客观状态，Memory 只表达 owner 的主观看法。
- 增加旁观、支持林慧兰和支持赵磊三条七日模拟路线。模拟器只调用公开 RunService 命令，具有单局/总调用数、消息数和墙钟时间上限，并生成无正文的 JSON/Markdown 指标报告。
- PostgreSQL 离线七日模拟发现：不同命令 ID 的相同 `world_step` 载荷会撞上 `(run_id, fingerprint)` 唯一约束。现已删除错误的唯一约束，只保留 `(run_id, command_id)` 幂等边界，并增加迁移与回归测试。
- 指定 Run 的 36 条私有 Memory 已完成真实回填；第二次执行为 0 新增、36 跳过。Repository 重开后的中文同义语义查询命中目标关系 Memory，8 个候选全部属于指定 owner。
- 真实 Day1 验收完成 NPC 行动、邀请、玩家加入与发言、NPC 决策与台词、18:00 强制关闭、离场沉淀、新 Memory 向量化和 Repository 重启恢复，所有门禁通过，临时 Run 已清理。
- 用户确认正式世界日由 10 分钟延长为 20 分钟；场景倍率、严格加载模型、API 换算、模拟驱动、设计文档与回归测试已同步。
- observer / pro_lin / pro_zhao 三条真实路线均到达 Day7 18:00、触发 7/7 事件、产生真实向量召回、完成 Repository 恢复并删除临时 Run。支持路线分别使林慧兰与赵磊从 `unknown` 变为 `support`，但整体仍为 `no_submission`，证明玩家影响有效且没有硬编码胜利。
- 真实数据驱动的最终参数为：普通连续回复链 2、参与者变化链 1、初始 Memory cache 1；支持路线验收采用 Day1 调查旧事与 Day7 询问最终立场的两阶段输入。

## 自动化证据

- PostgreSQL 与测试数据库均已迁移到最新版本，Alembic 未发现模型漂移。
- Fake 模型可以让三条路线推进到 Day7 18:00；PostgreSQL 模式完成后可重新打开 Repository 恢复 Run。
- 全量测试：155 passed，包含 PostgreSQL 集成测试。
- Ruff：通过。
- mypy：61 个源码文件通过。
- Git diff whitespace 与敏感信息扫描：通过。

## 真实七日结果

| 路线 | 对话/消息 | 玩家消息 | 召回/向量命中 | 章节立场 | 结局 |
|---|---:|---:|---:|---|---|
| observer | 14/45 | 0 | 2/16 | 无变化 | `no_submission` |
| pro_lin | 12/44 | 2 | 1/8 | `npc_001=support` | `no_submission` |
| pro_zhao | 14/51 | 2 | 1/7 | `npc_003=support` | `no_submission` |

三路线详细证据和限制见 `REAL_SEVEN_DAY_SIMULATION_RESULTS.md`。真实证据已完成，前端门禁解除。
