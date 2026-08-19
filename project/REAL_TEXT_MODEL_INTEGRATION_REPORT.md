# 方舟真实文本模型接入报告

- 验证日期：2026-08-20
- Base URL：`https://ark.cn-beijing.volces.com/api/plan/v3`
- 配置模型：`doubao-seed-2.0-lite`
- 凭据：本机 `.env` 中已轮换 Key；报告、日志和 Git 不保存 Key
- 验证工具：`core/backend/scripts/check_ark_connection.py --live`

## 六协议结果

| 协议 | 成功 | 首轮 Schema 成功 | 格式重试 | 耗时 ms | Token |
|---|---:|---:|---:|---:|---:|
| DailyActionDecision | 是 | 是 | 0 | 6257 | 477 |
| InvitationDecision | 是 | 是 | 0 | 946 | 300 |
| ChatDecision | 是 | 是 | 0 | 1320 | 1516 |
| SpeechGeneration | 是 | 是 | 0 | 1900 | 386 |
| SegmentSummary | 是 | 是 | 0 | 1563 | 435 |
| ExitConsolidation | 是 | 是 | 0 | 1613 | 1530 |

合计 6 次逻辑调用、6 次物理请求、0 次格式重试、4644 Token。所有响应都通过对应 Pydantic Schema；没有记录 Prompt 或回复正文。

## 结论与剩余项

真实连接和六类结构化协议已经通过。它证明适配器、模型名和基础 Prompt/Schema 兼容，但不等于完整 NPC 闭环已经通过；仍需真实执行一次 NPC 聊天、离场沉淀与 Repository 重启恢复，并完成三条七日路线。
