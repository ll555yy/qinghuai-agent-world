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

## 三条路线

```powershell
python core/backend/scripts/run_seven_day_simulation.py `
  --real --backend postgres --route all --runs 1 `
  --max-calls-per-run 600 --max-total-calls 1800 `
  --step-timeout-seconds 120 --run-timeout-seconds 600 `
  --output simulation_reports
```

也可单独运行 `observer`、`pro_lin` 或 `pro_zhao`。`--selected-agenda-id` 只能和匹配的单路线共同使用，不能直接修改 NPC 立场。加 `--keep-runs` 才保留模拟 Run。

长模拟开始前会用固定公开文本执行 Embedding 预检。每局报告包含世界事件、邀请、对话摘要、每名 NPC 发言与主动行动、Memory 向量/Graph 命中、Goal/关系/立场变化、首次 Schema 成功率、物理请求、延迟、Token、Day7 和 Repository 重启恢复状态。

真实局若未到 Day7、异常终止、没有聊天/消息/离场沉淀、没有 Memory 召回、Embedding 未启用或重启不能恢复，会保留报告并以非零状态退出，不能伪装成通过。
