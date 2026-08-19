# 后端第一阶段子智能体自审

- 实现范围：`core/backend` 与 `test/backend`
- 设计依据：`BACKEND_PHASE1_DESIGN.md`、`PROJECT_DESIGN.md` 的 D-053/D-054
- 实现边界：只实现进程内存后端核心；未连接数据库、未引入 OpenAI、未创建提示词/模型调用或前端文件
- Git：本次未创建 commit

## 实现摘要

- 建立 `core/backend/pyproject.toml`，统一 pytest、Ruff 和 mypy 配置；保留第一阶段 `environment.yml` 依赖边界。
- `scenario/` 加载并校验八个真实 YAML，检查 ID 唯一性、枚举、关系等级、Actor/Goal/Topic/Agenda 引用、章节时间和事件时间。
- 生成由 tuple/read-only mapping 与 frozen dataclass 组成的 `ScenarioRegistry`；运行实例只使用公开投影，不复制或暴露 NPC 私有数据。
- 实现纯领域 `WorldClock`：Day1 09:00 起始、08:00～18:00 活跃窗口、跨日跳转、暂停/恢复、Day7 18:00 章节结束和非法推进拒绝。
- 实现内存 `Run`、单 Run `asyncio.Lock`、`InMemoryRunRepository`、单调 `stateVersion/eventSeq`、公开事件和事件补取。
- 实现最多两场开放 Conversation、单场最多三人、Actor 一场限制、离开后少于两人自动关闭、后端生成会话序号/ID，以及 `commandId` 幂等处理。
- 实现健康检查、Run REST、虚拟时间 REST、Conversation REST、事件 REST 和 WebSocket 首帧快照/后续事件。
- 添加单元及集成测试，覆盖时间、会话约束、幂等、真实场景加载、信息隐藏、REST 错误和 WebSocket。

## 自审检查

- [x] `domain/` 未导入 FastAPI、Pydantic、YAML、OpenAI 或数据库包。
- [x] 公开快照和公开事件没有 `coreSecrets`、隐藏 Goal、私有 Memory、关系数值或 `authoringNote`。
- [x] API 业务错误使用统一 `{error: {code, message, details}}` 结构；资源不存在为 404，业务冲突为 409。
- [x] 失败的时间推进不会部分修改时钟；重复 `commandId` 不重复产生版本或事件。
- [x] Run 仓储只在进程内存中保存，无外部服务访问。
- [x] `core/backend/app/` 未创建 `ai`、`prompts`、`database` 等延后目录。

## 验收命令与结果

以下命令均使用项目 Conda Python：`E:\anaconda3\envs\qinghuai-chat\python.exe`。

```text
E:\anaconda3\envs\qinghuai-chat\python.exe -m pytest test/backend -q
19 passed, 1 warning in 0.91s

E:\anaconda3\envs\qinghuai-chat\Scripts\ruff.exe check core/backend test/backend
All checks passed!

E:\anaconda3\envs\qinghuai-chat\Scripts\mypy.exe core/backend/app
Success: no issues found in 30 source files

E:\anaconda3\envs\qinghuai-chat\python.exe -c "from core.backend.app.main import app; print(app.title)"
Qinghuai Chat Backend
```

pytest 仅出现当前环境 FastAPI/Starlette 与 httpx 的弃用警告，不影响退出码；没有访问互联网、数据库或 OpenAI。

## 未完成项

以下项目按第一阶段设计明确延后，未视为缺陷：后台真实时间循环和 Day 事件调度、NPC 位置/寻路/邀请与 AI 行为、聊天原文/片段/Memory 沉淀、PostgreSQL/pgvector 持久化、模型调用/提示词、前端工程及生产级认证、多进程广播和永久事件存储。
