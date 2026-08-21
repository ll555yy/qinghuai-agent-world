# 真实七日模拟最终结果

状态：observer、pro_lin、pro_zhao 三条路线均通过严格质量门。

## 最终验收矩阵

| 路线 | 对话 | 消息 | 玩家消息 | Memory 工具 | 召回 ID | 向量/Graph 命中 | 明确立场 | 结局 | 玩家任务 |
|---|---:|---:|---:|---:|---:|---:|---|---|---|
| observer | 14 | 45 | 0 | 2 | 16 | 16/0 | 0 | `no_submission` | n/a |
| pro_lin | 12 | 44 | 2 | 1 | 8 | 8/0 | `npc_001=support` | `no_submission` | `failed` |
| pro_zhao | 14 | 51 | 2 | 1 | 7 | 7/0 | `npc_003=support` | `no_submission` | `failed` |

三条路线都满足：

- 到达 Day7 18:00，7/7 世界事件已处理且无 skipped event；
- 有完整对话、NPC 台词、离场沉淀、Goal 与关系变化；
- 使用真实火山方舟文本模型和 `doubao-embedding-vision-251215` 2048 维向量；
- 至少一次 owner-safe Agent Memory 工具调用和真实向量命中；
- PostgreSQL Repository 关闭重开后 Run 可恢复；
- 验收完成后临时 Run 删除，报告不保存 Prompt、对话正文、Memory 正文或密钥。

## 结果解释

三条合格样本都得到 `no_submission`，支持路线的玩家任务也都为 `failed`。这不表示玩家消息无效：林慧兰和赵磊分别在自己的支持路线中由 `unknown` 变为带本人台词证据的 `support`。整体提交还要求至少三名 NPC 的正向总体立场以及周慎之授权，验收脚本没有替其他角色写入态度，所以不会保证胜利。

Graph 扩展命中为 0 表示本次查询的向量种子已直接覆盖返回上限；Graph 邻居扩展、owner 隔离和持久化由独立自动化测试覆盖，不要求每次真实查询都人为制造 Graph 命中。

## 数据驱动调优结论

- 完整世界日为现实 20 分钟；每 2 秒推进 1 个虚拟分钟，08:00～18:00 共 1200 秒。
- 普通消息/开场触发的连续 NPC 回复链为 2，参与者变化链为 1。
- 初始会话 Memory cache 为 1；当天事件走 fresh context，旧事证据不足时由 Agent 按需召回。
- 支持路线采用两阶段输入：Day1 调查旧事与顾虑，Day7 明确询问总体提交和选定 Agenda 的最终立场。
- 方舟或 Embedding 短时不可用时使用正常安全降级；Memory 正文先落库，缺失向量可幂等 backfill。合格样本仍必须满足真实召回、立场、恢复和清理门槛。

完整调参历史见 `PROMPT_GAMEPLAY_TUNING_LOG.md`。
