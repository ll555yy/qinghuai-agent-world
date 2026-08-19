# 初始关系 Memory 转换说明

关系数值是客观权威状态；`scenario_seed` Memory 保存 owner NPC 的主观看法。内容只来自已确认人设、Goal、秘密和关系设定，`authoringNote` 不进入运行时提示词，也不会因补 Memory 改变关系数值。

## 25 条有向覆盖

| Owner | 四名 NPC Target | 玩家 Target |
|---|---|---|
| npc_001 | `memory_seed_rel_npc_001_npc_002` ～ `_005` | `memory_seed_001` |
| npc_002 | `memory_seed_rel_npc_002_npc_001/_003/_004/_005` | `memory_seed_003` |
| npc_003 | `memory_seed_rel_npc_003_npc_001/_002/_004/_005` | `memory_seed_006` |
| npc_004 | `memory_seed_rel_npc_004_npc_001/_002/_003/_005` | `memory_seed_008` |
| npc_005 | `memory_seed_rel_npc_005_npc_001/_002/_003/_004` | `memory_seed_009` |

20 条 NPC→NPC 使用由两端 Actor ID 派生的稳定 ID；5 条 NPC→玩家复用原有玩家印象，避免同义 Memory 重复召回。每条只表达一个核心认知，owner 是 fromActor，actorIds 包含 toActor；只有确有依据时才连接 Goal 或 Topic。

加载器按 `(ownerNpcId, targetActorId)` 强制检查 25 条边，缺一条就拒绝启动。检索先固定 `run_id + owner_npc_id`，另一 NPC 的高相似 Memory 不能进入候选，Graph 每一跳也再次检查 owner。
