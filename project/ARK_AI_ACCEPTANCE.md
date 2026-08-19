# 火山方舟 AI 适配器主会话验收

> 历史阶段报告：本文记录 2026-08-18 仅完成适配器时的边界，不代表当前实现状态。六类 Agent 协议现已进入 NPC 主循环；真实联网验收状态以 `REAL_AI_EMBEDDING_SIMULATION_ACCEPTANCE.md` 为准。

- 状态：通过
- 日期：2026-08-18
- 实现与自审：Luna Max 子智能体
- 最终验收：主会话

## 交付范围

- 增加与提供方无关的 `TextModel` 端口和请求、响应、错误对象；
- 增加火山方舟 Agent Plan 适配器；
- 默认模型为 `doubao-seed-2.0-lite`；
- 增加只读 `GET /api/ai/status`；
- 增加 `.env.example` 和一次性本地连通性脚本；
- 使用 Mock 覆盖成功、缺少配置、认证失败、限流、超时和空响应；
- 当时未接入 NPC 主循环、数据库或前端；这些是该历史阶段的范围说明。

## 主会话检查与修正

1. 将 `ARK_API_KEY` 在 `ArkSettings` 构造时读取并固定，避免应用启动后环境变量变化导致状态接口与实际客户端配置不一致；字段不进入对象 `repr`。
2. 为模型输入增加最小正常校验：至少一条消息、角色仅允许 `user | assistant`、温度范围为 0–2、输出 Token 数必须为正数。
3. 保持简化兜底：仅对连接、超时、限流和服务端错误最多重试一次；认证和参数错误不重试；不增加熔断、多模型复核、复杂退避或自动修复。
4. 确认状态接口、错误信息和日志不返回 API Key、Authorization Header、完整 Prompt 或 NPC 秘密。

## 主会话复验

```text
pytest test/backend -q
33 passed, 1 third-party deprecation warning

ruff check core/backend test/backend
All checks passed!

mypy core/backend/app
Success: no issues found in 36 source files

secret scan
SECRET_SCAN_NO_MATCHES
```

## 边界结论

- 适配器已具备下一阶段接入 NPC 决策编排的稳定边界；
- 当前没有真实 API Key，因此没有发送付费或联网模型请求；
- 聊天中曾出现的旧 Key 不得使用，必须先在方舟控制台轮换，再由用户放入本机环境变量；
- 第一版继续遵循“完整可玩优先、常见故障正常兜底、极低概率风险不提前复杂化”。
