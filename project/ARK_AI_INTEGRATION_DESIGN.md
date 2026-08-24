# 火山方舟 AI 适配器设计

- 状态：已确认接入方案
- 日期：2026-08-18
- 前置条件：后端权威世界核心与结构化协议已通过自动化测试

## 1. 目标

在不改变领域层和内存世界核心的前提下，为后端增加一个可替换的文本模型端口，并实现火山方舟 Agent Plan 适配器。初期只验证安全配置、连通性、文本请求、超时和错误转换，不立即接入六类 NPC 决策流水线。

## 2. 固定配置

```text
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3
ARK_MODEL=doubao-seed-2.0-lite
ARK_API_KEY=<仅保存在本机 .env 中>
```

- `ARK_API_KEY` 必填且没有默认值。
- `.env` 已由 `.gitignore` 排除。
- 仓库只创建 `.env.example`，值必须是占位符。
- 日志、异常、状态接口和测试快照不得包含 Key 或完整 Authorization Header。

## 3. 目录

```text
core/backend/app/ai/
  __init__.py
  port.py              与提供方无关的 TextModel 协议
  models.py            请求、响应和用量对象
  errors.py            内部 AI 错误
  ark_client.py        火山方舟实现

test/backend/unit/
  test_ark_client.py

.env.example
```

## 4. 端口

领域层不直接调用 SDK。适配器实现以下最小异步接口：

```python
class TextModel(Protocol):
    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult: ...
```

请求只包含：

- `system_prompt`
- `messages`
- `temperature`
- `max_output_tokens`
- `request_id`

结果只包含：

- `text`
- `provider`
- `model`
- 可选 Token 用量
- 提供方请求 ID

任何 SDK 对象都不能越过适配器边界。

## 5. 方舟实现

- 使用 OpenAI 兼容 Python 客户端并设置 Agent Plan Base URL。
- 初期模型统一取 `ARK_MODEL`，默认 `doubao-seed-2.0-lite`。
- 每次请求有明确连接与总超时。
- 只对连接中断、服务端 5xx 和限流进行最多一次重试；认证失败和参数错误不重试。
- 将认证失败、额度/限流、超时、提供方不可用、空响应和格式错误转换为项目内部枚举错误。
- 记录 request ID、模型、耗时和 Token；不记录完整提示词、角色秘密或 API Key。

## 6. 状态与验证

- 提供只读 `/api/ai/status`，只返回 `configured`、provider、model 和 base URL host，不发起付费模型调用。
- 不提供公开的任意 Prompt 测试接口，防止被滥用消耗额度。
- 提供手动命令行连通性脚本；只有本机已经设置 `ARK_API_KEY` 时才执行一次固定短提示。
- 自动测试全部 Mock 网络，不访问火山方舟。

## 7. 验收

- 未设置 Key 时应用仍可启动，`configured=false`；只有实际调用模型时返回明确配置错误。
- Mock 成功、认证失败、限流、超时、空响应和 Key 脱敏测试全部通过。
- `domain/` 不导入 `openai` 或 `ai.ark_client`。
- Git 搜索不存在真实 `ark-` 密钥。
- 用户轮换并自行设置新 Key 后，再由主会话执行一次手动连通性测试；旧密钥永不使用。
