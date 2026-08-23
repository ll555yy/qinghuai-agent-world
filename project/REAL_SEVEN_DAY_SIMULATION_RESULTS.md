# 真实七日模拟最终结果

状态：最终严格可达性验收已完成。旁观、联盟成功、低投入失败三条路线均取得 3/3 个不同 seed 的新格式真实有效样本，最终聚合结果为 `complete=true`、`requirementFailures=[]`。上一批单目标结果继续保留为历史基线。

## 上一批真实验收矩阵（单目标基线）

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

这批数据只能证明玩家能改变单个 NPC 的可追溯立场，不能作为多 NPC 联盟协商成功的证据。旧的 `pro_lin`/`pro_zhao` 样本主要是单目标、两条玩家消息的基线，保留它们是为了对比本阶段的投入量和玩法可达性，不把历史 `no_submission` 当作新路线的预期硬编码结果。

Graph 扩展命中为 0 表示本次查询的向量种子已直接覆盖返回上限；Graph 邻居扩展、owner 隔离和持久化由独立自动化测试覆盖，不要求每次真实查询都人为制造 Graph 命中。

## 数据驱动调优结论

- 完整世界日为现实 20 分钟；每 2 秒推进 1 个虚拟分钟，08:00～18:00 共 1200 秒。
- 普通消息/开场触发的连续 NPC 回复链为 2，参与者变化链为 1。
- 初始会话 Memory cache 为 1；当天事件走 fresh context，旧事证据不足时由 Agent 按需召回。
- 支持路线采用两阶段输入：Day1 调查旧事与顾虑，Day7 明确询问总体提交和选定 Agenda 的最终立场。
- 方舟或 Embedding 短时不可用时使用正常安全降级；Memory 正文先落库，缺失向量可幂等 backfill。每局必须通过真实 Embedding 预检，按需召回能力由真实聊天验收和有召回的七日样本共同证明，不强迫每个 NPC 在每局都调用记忆工具。

完整调参历史见 `PROMPT_GAMEPLAY_TUNING_LOG.md`。

## 本阶段最终可达性验收

路线语义为：`observer` 旁观，`pro_lin` 基于公开信息的七日多 NPC 联盟协商，`pro_zhao` 低投入失败对照。最终矩阵只纳入新格式、真实方舟、PostgreSQL、完整成本、已恢复且已删除临时 Run 的样本：

| 路线 | 要证明的行为 | 最少样本 | 必记指标 | 当前状态 |
|---|---|---:|---|---|
| `observer` | 无玩家发言时世界独立推进 | 3 seeds | 玩家发言、立场变化人数、Goal 完成率、分支、Token、估算成本 | 3/3 有效，通过 |
| `pro_lin` | 玩家与多名 NPC 协商，真实条件汇总后达成成功分支 | 3 seeds | 同上，并检查成功分支是否真实可达 | 3/3 有效，通过 |
| `pro_zhao` | 低投入单点支持不足以形成整体方案，失败分支自然出现 | 3 seeds | 同上，并检查失败分支 | 3/3 有效，通过 |

所有路线都必须遵守：不读取 NPC 私有立场或 Goal 作为玩家策略输入；不由脚本写入 NPC 立场、Goal、授权或最终分支；报告只保留脱敏统计，不保存聊天正文、Prompt、coreSecrets 或完整 Memory。

## 新联盟路线 pilot（最终 Prompt 形成前后）

本阶段先运行真实 `pro_lin` pilot 定位失败原因，再用最终 Prompt 进行多 seed 复验，并额外保留一次并发压力诊断。所有样本均使用真实方舟文本模型、真实 PostgreSQL 和真实 Embedding，并在结束后确认临时数据库行已删除。

| Seed | Prompt 版本 | 玩家发言 | 改变立场 NPC | Goal 完成率 | 周慎之授权 | 结局 | 玩家任务 | 物理请求 | 折算成本 CNY | 清理 |
|---:|---|---:|---:|---:|---|---|---|---:|---:|---|
| 20260822 | 修正前 | 6 | 4 | 0.0 | `none` | `no_submission` | `failed` | 251 | 1.292276 | 数据库实查已删除；旧报告受 Repository 身份缓存影响误记为 `false` |
| 20260823 | 最终版 | 7 | 5 | 0.0 | `conditional` | `compromise_submitted` | `partial` | 261 | 1.481516 | `true` |
| 20260824 | 最终版 | 7 | 5 | 0.045455 | `conditional` | `compromise_submitted` | `completed` | 252 | 1.407840 | `true` |
| 20260829 | 并发压力诊断 | 4 | 3 | 0.0 | `conditional` | `compromise_submitted` | `partial` | 223 | 0.886526 | `true`，因仅 4 条玩家发言不计入严格矩阵 |
| 20260832 | 最终版低并发复验 | 5 | 3 | 0.0 | `conditional` | `compromise_submitted` | `completed` | 251 | 1.230968 | `true` |

20260823 的五名总体立场为：林慧兰 `support`、沈星遥 `conditional`、赵磊 `support`、陈月 `support`、周慎之 `conditional`；青槐文社为 `partially_adopted`，玩家任务 `partial`。20260824 的五名总体立场全部为 `support`，青槐文社达到 `core_adopted`，玩家任务 `completed`。两局中周慎之均给出 `conditional` 授权，最终都由生产结局规则计算为 `compromise_submitted`；脚本没有写入任何立场或分支。

修正前样本已经让前四名 NPC 全部从 `unknown` 变为 `support`，但周慎之面对玩家直接提问仍选择静默，导致没有授权。最终 Prompt 因此只增加一条通用对话规则：NPC 被仍在会话内的玩家直接提问时必须用台词回答，但仍可自主拒绝、反对或提出条件。这修复的是“聊天不回应”，不是预设支持。

折算成本使用真实 token 用量，按运行器配置的公开按量单价估算；当前调用地址为 Agent Plan，因此它不是账户实际账单。并发压力诊断 `20260829` 虽然也到达成功分支，但因方舟波动只写入 4/7 条路线话术，被质量门正确排除；低并发复验 `20260832` 以 5 条玩家发言、3 名 NPC 立场变化再次到达成功分支，说明成功不依赖脚本替 NPC 写入态度，也不要求每个 NPC 都接受玩家邀请。

最终机器可读矩阵位于 `simulation_reports/final_gameplay_evidence/seven_day_gameplay_evidence.json`，可读版位于同目录的 Markdown 文件。它要求每条路线至少 3 个不同 seed且 3/3 全部有效，不再用旧式 `legacy_text_tokens_only` 报告凑数。最终汇总如下：

| 路线 | 有效样本 | 玩家发言总数 | 平均改变立场 NPC | 平均 Goal 完成率 | 结局分布 | 折算成本 CNY |
|---|---:|---:|---:|---:|---|---:|
| `observer` | 3/3 | 0 | 1.0 | 0.018519 | `no_submission × 3` | 2.466583 |
| `pro_lin` | 3/3 | 19 | 4.333333 | 0.015152 | `compromise_submitted × 3` | 4.120324 |
| `pro_zhao` | 3/3 | 6 | 3.0 | 0.096032 | `no_submission × 3` | 3.141135 |

九个最终样本总折算成本为 `9.728042 CNY`。每个样本都达到 Day7 18:00、处理七个世界事件、通过真实 Embedding 预检、记录完整成本、完成 Repository 重启恢复并删除临时 Run；最终数据库 `chapter_runs` 数量恢复到运行前基线 23。
