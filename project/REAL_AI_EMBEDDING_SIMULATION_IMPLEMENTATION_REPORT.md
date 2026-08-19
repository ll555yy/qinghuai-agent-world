# 真实 AI、Embedding 与七日模拟实施报告

状态：真实文本六协议通过，真实 Embedding 维度已确认；回填、语义检索与七日模拟执行中。

## 已实现

- 方舟文本模型的每日行动、邀请、聊天决策、台词、分段摘要和离场沉淀六类协议已接入 Agent 决策链；所有结果使用严格 JSON 对象解析，格式失败至多重试一次。
- 增加显式 `--live` 的六协议验收工具。默认只检查配置和协议，不联网；报告不保存 Prompt、回复正文、密钥或 NPC 私密信息。
- 增加方舟 Agent Plan Embeddings 适配器、2048 维数据库迁移、新 Memory 事务外索引、幂等批量回填、内容变化失效和保存时向量保留机制。真实探测解析模型为 `doubao-embedding-vision-251215`，返回 2048 维。完整 float32 向量保存在 `vector(2048)`，HNSW 使用 `halfvec(2048)` 表达式索引。
- 混合召回继续以 `run_id + owner_npc_id` 作为硬隔离边界，并统计向量命中与 Graph 命中。
- 25 条有向初始关系全部拥有 NPC 私有 `scenario_seed` Memory；关系数值仍是客观状态，Memory 只表达 owner 的主观看法。
- 增加旁观、支持林慧兰和支持赵磊三条七日模拟路线。模拟器只调用公开 RunService 命令，具有单局/总调用数、消息数和墙钟时间上限，并生成无正文的 JSON/Markdown 指标报告。
- PostgreSQL 离线七日模拟发现：不同命令 ID 的相同 `world_step` 载荷会撞上 `(run_id, fingerprint)` 唯一约束。现已删除错误的唯一约束，只保留 `(run_id, command_id)` 幂等边界，并增加迁移与回归测试。

## 自动化证据

- PostgreSQL 与测试数据库均已迁移到最新版本，Alembic 未发现模型漂移。
- Fake 模型可以让三条路线推进到 Day7 18:00；PostgreSQL 模式完成后可重新打开 Repository 恢复 Run。
- 全量测试：137 passed。
- Ruff：通过。
- mypy：61 个源码文件通过。
- Git diff whitespace 与敏感信息扫描：通过。

## 仍需真实运行

本机 `.env` 已配置轮换后的 Key，且真实六协议已全部通过。Coding Plan 地址对当前 Agent Plan Key 返回 401；改用 Agent Plan 地址后，Embedding 请求成功并确认 2048 维。以下事项仍未完成：

1. 应用 2048 维迁移并为真实 Run 回填向量，验证语义命中、owner 隔离和数据库重启后的向量持久化。
2. 完成至少一次真实 NPC 聊天、离场沉淀与 Repository 重启恢复。
3. 运行三条真实七日路线。
4. 根据三份报告调整 Prompt 与玩法参数，并至少复跑受影响路线。

真实证据完成前，前端保持门禁状态；也不宣称真实模型下的玩法质量已经验收。
