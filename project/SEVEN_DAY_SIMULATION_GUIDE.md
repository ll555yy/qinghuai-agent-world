# 七日真实模拟工具使用说明

真实模拟只允许显式运行，且强制使用 PostgreSQL、真实文本模型和真实 Embedding。报告不保存 Prompt、聊天全文、coreSecrets 或完整 Memory。

## 前置命令

```powershell
cd core/backend
alembic upgrade head
alembic check
cd ../..
python core/backend/scripts/check_ark_connection.py --live
python core/backend/scripts/check_ark_embedding.py --live
python core/backend/scripts/run_real_chat_acceptance.py --live
```

`run_real_chat_acceptance.py` 先跑一局 Day1 小闭环，并以脱敏门禁验证 NPC 主动邀请、玩家加入发言、NPC 离场沉淀、Conversation Memory 向量和 Repository 重启恢复；默认完成后删除验收 Run。

## 三条路线与本阶段验收目标

本项目同时保留历史单目标基线，并将下一批真实模拟的路线定义为：

| 路线 | 玩家行为 | 验收意图 |
|---|---|---|
| `observer` | 不发言、不主动干预，只观察七日世界 | 验证没有玩家输入时世界仍能独立推进 |
| `pro_lin` | 依据公开信息，在七天内依次接触多名 NPC，围绕林慧兰的文社方案协调条件、汇总分歧，并在截止前向周慎之确认授权 | 验证玩家可以通过多 NPC 联盟协商实际改变整体结局，而不只是改变一个人的立场 |
| `pro_zhao` | 低投入、单点式地支持赵磊的商业方案，不持续协调其他 NPC 的条件 | 作为失败对照，验证投入不足时可以自然得到未提交/失败分支 |

`pro_lin` 的玩家话术只使用玩家已经通过公开事件、公开对话和自己的聊天记录获得的信息。脚本不得读取任何 NPC 的私有 `overall_stance`、`agenda_stance`、Goal 内部状态或私有 Memory，也不得直接写入立场、Goal 或结局。`pro_zhao` 的失败是验收期望，不是后端强制写入的结局；最终分支由 NPC 自己产生的总体立场与周慎之授权计算，Goal 完成率作为独立玩法指标报告。

本阶段每条路线至少运行 3 个不同 seed。三条路线的结果必须分别统计，不能用一次 `--route all` 的单个样本代替 3-seed 验收。

```powershell
python core/backend/scripts/run_seven_day_simulation.py `
  --real --backend postgres --route observer --runs 3 --seed 20260822 `
  --max-calls-per-run 600 --max-total-calls 1800 `
  --step-timeout-seconds 900 --run-timeout-seconds 5400 `
  --output simulation_reports
```

再分别将 `--route observer` 替换为 `pro_lin`、`pro_zhao` 各运行 3 个不同 seed。`--selected-agenda-id` 只能和匹配的单路线共同使用，不能直接修改 NPC 立场。加 `--keep-runs` 才保留模拟 Run。

长模拟开始前会用固定公开文本执行 Embedding 预检。每局报告包含世界事件、邀请、对话摘要、每名 NPC 发言与主动行动、Memory 向量/Graph 命中、Goal/关系/立场变化、首次 Schema 成功率、物理请求、延迟、Token、Day7 和 Repository 重启恢复状态。

本阶段每个 route 的汇总报告还必须包含以下可比较指标：

- `player_speech_count`：玩家实际成功写入聊天的发言数；
- `distinct_stance_changed_count`：由 NPC 自己的台词证据驱动、至少发生过一次变化的 NPC 数量；
- `goal_completion_rate`：该 Run 中 `achieved` Goal 数除以全部 Goal 数；
- `branch`：由最终真实状态计算出的结局分支；
- `tokens`：输入、输出及总 Token；
- `estimated_cost`：按运行配置中的方舟输入/输出单价估算，未配置价格时明确标记为 `n/a`，不能伪造金额。

汇总时同时保留每个 seed 的明细和 route 的成功/失败/旁观比例。质量门应区分“基础管线完整”（到达 Day7、记忆、恢复、清理）与“玩法可达性”：`pro_lin` 至少需要出现真实成功分支样本，`pro_zhao` 至少需要保留真实失败分支样本，`observer` 只验证无玩家干预的世界推进，不要求它替玩家完成目标。

多个批次完成后使用脱敏汇总器生成最终矩阵：

```powershell
python core/backend/scripts/summarize_seven_day_evidence.py `
  simulation_reports/<batch-a>/seven_day_simulation_batch.json `
  simulation_reports/<batch-b>/seven_day_simulation_batch.json `
  --output simulation_reports/gameplay_evidence
```

汇总器会拒绝重复 route/seed，并自动检查：每条路线至少 3 个不同 seed，而且三条路线都必须各有 3 个技术质量门通过且符合路线预期的新格式真实样本。每个样本必须来自 `real + postgres` 批次，达到 Day7 18:00、处理全部七个世界事件、完成 Embedding 预检、提供非空 Goal 完成率与完整成本，并证明 Repository 恢复和临时 Run 删除。旧式 `legacy_text_tokens_only` 报告只作历史参考，不能计入最终通过数。矩阵未完成时返回非零退出码，不能把缺样本的报告当成最终证据。

真实局若未到 Day7、异常终止、没有聊天/消息/离场沉淀、Embedding 未启用或重启不能恢复，会保留报告并以非零状态退出，不能伪装成通过。长期记忆是 Agent 按需调用的工具，因此不是每个七日样本都强制产生召回；真实向量召回能力由独立聊天验收与专项测试证明。
