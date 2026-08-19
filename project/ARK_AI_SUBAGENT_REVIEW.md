# 火山方舟 AI 适配器子智能体自审

> 历史阶段记录：本文描述适配器刚完成时的状态。当前六类协议已接入 NPC 主循环并通过真实方舟验证，现状以 `REAL_TEXT_MODEL_INTEGRATION_REPORT.md` 为准。

- 实现范围：`core/backend/app/ai`、AI 状态路由、`.env.example`、本地连通性脚本和 Mock 测试
- 设计依据：`ARK_AI_INTEGRATION_DESIGN.md`、`PROJECT_DESIGN.md` 的 D-055/D-056、`PROJECT_RULES.md`
- SDK：本机已安装 `openai 2.54.0`
- Git：本次未创建 commit

## 实现摘要

- 新增 provider-independent `TextModel` 协议，以及 Pydantic 请求、响应和 Token 用量模型。
- 新增 `ArkClient`，通过 OpenAI 兼容 `AsyncOpenAI` 调用方舟 Agent Plan；默认模型为 `doubao-seed-2.0-lite`，默认 Base URL 为 `https://ark.cn-beijing.volces.com/api/plan/v3`。
- API Key 只从 `ARK_API_KEY` 环境变量读取，无默认值；缺 Key 时应用仍能启动，实际生成请求返回 `ai_not_configured`。
- 适配器设置明确连接/总超时，关闭 SDK 内置重试并只对连接中断、超时、限流和 5xx 做最多一次瞬时重试。
- 将认证失败、限流/额度、超时、连接/服务不可用、无效请求、空响应和无效响应转换为 `AIErrorCode`；异常消息不携带原始 Key、Authorization Header 或完整 Prompt。
- 成功与失败日志只记录 request ID、提供方、模型、耗时、Token 总量或内部错误码，不记录 Prompt 和密钥。
- 新增只读 `GET /api/ai/status`，只返回 `configured`、provider、model、`baseUrlHost`，不发起模型调用。
- 新增 `core/backend/scripts/check_ark_connection.py`，仅在本机已设置 `ARK_API_KEY` 时发送一次固定短提示；当前环境未设置 Key，因此本次没有执行真实请求。
- 未将 AI 客户端接入 NPC 主循环、Run 状态、领域层、数据库或前端。

## 自审检查

- [x] `domain/` 不导入 `openai` 或 `ai.ark_client`。
- [x] `ARK_API_KEY` 不出现在源码默认值、YAML、测试、日志、状态接口或异常消息中。
- [x] `.env` 已由现有 `.gitignore` 排除；仓库只新增占位符 `.env.example`。
- [x] 状态接口是只读接口，不提供任意 Prompt 测试入口。
- [x] 自动测试使用 Fake 客户端，未访问火山方舟或其他外部网络。
- [x] 未使用或记录聊天中出现的旧 Key；本次只检查并读取当前进程的 `ARK_API_KEY` 环境变量，当前环境为未设置。

## 验收命令与结果

以下命令使用项目 Conda 环境：`E:\anaconda3\envs\qinghuai-chat\python.exe`。

```text
E:\anaconda3\envs\qinghuai-chat\python.exe -m pytest test/backend -q
28 passed, 1 warning in 0.87s

E:\anaconda3\envs\qinghuai-chat\Scripts\ruff.exe check core/backend test/backend
All checks passed!

E:\anaconda3\envs\qinghuai-chat\Scripts\mypy.exe core/backend/app
Success: no issues found in 36 source files

E:\anaconda3\envs\qinghuai-chat\python.exe -c "from core.backend.app.main import app; print(app.title)"
Qinghuai Chat Backend

Secret scan (ark-/sk-/AKIA/Bearer value patterns; design docs excluded)
SECRET_SCAN_NO_MATCHES
```

pytest 只有当前 FastAPI/Starlette 与 httpx 的弃用警告，不影响退出码。
未设置 Key 时运行 `python core/backend/scripts/check_ark_connection.py` 会输出未配置提示并以退出码 2 结束，不发送请求。

## 未完成项

- 未执行真实方舟连通性测试，因为当前环境没有 `ARK_API_KEY`；由用户轮换并自行设置新 Key 后，再由主会话手动执行一次固定短提示连通性检查。
- 六类 NPC 决策、结构化输出、Embedding、提示词编排和 NPC 主循环接入按设计延后。
