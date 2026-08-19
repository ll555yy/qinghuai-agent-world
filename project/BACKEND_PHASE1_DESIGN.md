# 后端第一阶段设计：内存世界核心

- 状态：已确认实施范围
- 日期：2026-08-18
- 设计者：主会话
- 实现者：Luna Max 子智能体
- 验收者：主会话

本阶段同时受 `PROJECT_RULES.md` 约束：实现完整核心路径即可，不为了未来数据库、AI、多进程或公网部署增加防御性代码。

## 1. 阶段目标

建立一个能启动、能校验场景、能创建世界运行实例、能推进时间、能管理聊天生命周期并能自动测试的 Python 后端骨架。

本阶段刻意不实现：

- PostgreSQL、pgvector、SQLAlchemy、psycopg、Alembic 或任何数据库连接；
- OpenAI SDK、模型调用、提示词、Embedding、AI 决策或模拟回复；
- React、Phaser、Node、pnpm 或任何前端文件；
- NPC 自动移动、寻人、邀请判断、台词生成、Memory 总结和 Day7 AI 立场分析。

所有运行状态只保存在单进程内存中，服务重启后丢失。这是本阶段的明确边界，不属于缺陷。

## 2. 第一阶段交付能力

### 2.1 服务启动

- FastAPI 应用可以通过 Uvicorn 启动。
- 应用使用 lifespan 在启动时加载并校验 `core/scenario/` 中的八个 YAML 文件。
- 场景配置无效时启动失败，并给出包含文件和字段位置的可读错误。
- 提供健康检查接口，区分进程存活与场景是否成功加载。

### 2.2 场景加载

加载以下配置：

- `NPC_PERSONAS.yaml`
- `PLAYER_PROFILE.yaml`
- `INITIAL_TOPICS.yaml`
- `INITIAL_GOALS.yaml`
- `INITIAL_RELATIONSHIPS.yaml`
- `INITIAL_MEMORIES.yaml`
- `WORLD_EVENTS_DAY1_7.yaml`
- `CHAPTER_AGENDAS.yaml`

启动校验至少覆盖：

- Actor、Goal、Topic、Agenda ID 唯一；
- 所有 Actor、Goal、Topic 引用存在；
- 关系边不能指向不存在的角色；
- Agenda 必须连接存在的公开长期 Goal；
- 世界时间配置与 Day1～Day7 事件时间可解析；
- 枚举值和关系等级符合已确认规则。

场景加载器生成不可变的 `ScenarioRegistry`。运行实例可以引用它，但不能修改原始配置。

### 2.3 内存世界运行实例

- `POST /api/runs` 创建一个新的世界运行实例。
- 每个实例拥有 `runId`、`stateVersion`、`eventSeq`、世界时间、玩家任务选择、角色运行状态和 Conversation 集合。
- 初始时间取 Day1 09:00；玩家任务可以是一个有效 `agendaId` 或 `null`。
- `GET /api/runs/{runId}` 返回公开快照。
- 不向公开快照暴露 NPC 的隐藏 Goal、秘密、私有 Memory、关系数值或作者注释。
- 使用进程内 `InMemoryRunRepository` 保存实例，并以 `asyncio.Lock` 保护单个 Run 的复合修改。

### 2.4 世界时间

实现纯领域对象 `WorldClock`：

- 活跃时段 08:00～18:00；
- Day1 从 09:00 开始；
- 一现实分钟映射一虚拟小时的配置由场景提供，本阶段接口直接传入虚拟分钟，不读取真实墙钟；
- 超过 18:00 跳到次日 08:00；
- Day7 18:00 后进入 `chapter_ended`；
- 支持 `running | paused | chapter_ended`；
- 时间倒退、越界和章节结束后继续推进必须拒绝。

提供测试/调试接口 `POST /api/runs/{runId}/time/advance`。它只接受虚拟分钟数，不实现后台自动计时器。

### 2.5 Conversation 状态机

实现不依赖 AI 的纯领域规则：

- 世界最多两场开放 Conversation；
- 单场最多三名参与者；
- 单个 Actor 同时最多属于一场 Conversation；
- 创建 Conversation 至少需要两名不同且存在的 Actor；
- 加入前校验会话未满、Actor 不在其他会话；
- 离开只代表 `leave_chat`，不从世界移除 Actor；
- 离开后少于两人则关闭 Conversation；
- Conversation ID、创建序号和关闭原因由后端生成；
- 同一命令携带相同 `commandId` 时不得重复应用。

提供仅供当前原型调用的 REST 命令：

- `POST /api/runs/{runId}/conversations`
- `POST /api/runs/{runId}/conversations/{conversationId}/participants`
- `DELETE /api/runs/{runId}/conversations/{conversationId}/participants/{actorId}`

这些接口让测试和未来前端能够驱动状态机，不代表玩家最终 UI 直接拥有所有管理权限。

### 2.6 事件与 WebSocket

- 每次成功修改 Run 时递增 `stateVersion` 和 `eventSeq`。
- 产生带有 `runId`、`eventSeq`、`stateVersion`、`eventType` 和 `payload` 的公开事件。
- `GET /api/runs/{runId}/events?afterSeq=` 用于测试和断线补取内存事件。
- `WS /ws/runs/{runId}` 连接成功后先发送公开快照，再发送后续公开事件。
- 第一阶段不做用户认证、多进程广播和永久事件存储。

## 3. 目录结构

```text
core/backend/
  environment.yml
  pyproject.toml
  app/
    __init__.py
    main.py
    settings.py
    api/
      __init__.py
      router.py
      routes/
        health.py
        runs.py
        conversations.py
        websocket.py
    contracts/
      __init__.py
      common.py
      run.py
      conversation.py
      event.py
    domain/
      __init__.py
      errors.py
      clock.py
      conversation.py
      run.py
    orchestration/
      __init__.py
      run_service.py
      event_hub.py
    persistence/
      __init__.py
      run_repository.py
      in_memory.py
    scenario/
      __init__.py
      models.py
      loader.py
      registry.py

test/backend/
  conftest.py
  unit/
    test_clock.py
    test_conversation.py
    test_scenario_loader.py
  integration/
    test_health_api.py
    test_run_api.py
    test_conversation_api.py
    test_websocket.py
```

禁止在 `core/backend/app/` 中创建 `ai`、`prompts`、`database` 等本阶段无实现目录。

## 4. 分层边界

- `domain/`：纯 Python 规则；不能导入 FastAPI、Pydantic、YAML、OpenAI 或数据库包。
- `contracts/`：API 输入输出的 Pydantic 模型；不能包含世界规则。
- `scenario/`：读取 YAML、校验交叉引用并建立 Registry。
- `persistence/`：定义 Run 仓储协议与内存实现；领域层不知道仓储细节。
- `orchestration/`：组织锁、仓储、状态机和事件发布。
- `api/`：只转换 HTTP/WebSocket 请求与领域异常，不直接修改 Run。

依赖方向：

```text
api → orchestration → domain
             ↓
        persistence

scenario → contracts/registry data
```

## 5. 错误协议

REST 错误统一为：

```json
{
  "error": {
    "code": "conversation_full",
    "message": "Conversation already has three participants.",
    "details": {}
  }
}
```

第一阶段至少定义：

- `run_not_found`
- `actor_not_found`
- `agenda_not_found`
- `conversation_not_found`
- `conversation_full`
- `conversation_limit_reached`
- `actor_already_in_conversation`
- `invalid_time_advance`
- `chapter_already_ended`
- `duplicate_command`

业务冲突使用 409，不存在使用 404，输入格式错误使用 FastAPI/Pydantic 的 422。

## 6. 测试要求

### 6.1 单元测试

- 时间跨日、暂停、Day7 结束和非法推进；
- 两场上限、三人上限、一人一场、少于两人自动关闭；
- `commandId` 幂等；
- 八个真实 YAML 加载成功；
- 修改副本不能污染 `ScenarioRegistry`；
- 构造无效引用时返回确定性错误。

### 6.2 集成测试

- lifespan 启动并报告场景已加载；
- 创建 Run、读取公开快照、推进时间；
- Conversation 创建、加入、离开和错误状态码；
- 每次有效修改只增加一次版本和事件序号；
- WebSocket 首帧是快照，之后能收到对应 Run 的事件；
- API 响应不得包含 `coreSecrets`、隐藏 Goal、私有 Memory 或关系数值。

全部测试不得访问互联网、数据库或 OpenAI。

## 7. 工程配置

`environment.yml` 第一阶段只安装：

- Python 3.12
- FastAPI
- Uvicorn
- Pydantic v2
- PyYAML
- pytest
- AnyIO
- HTTPX
- Ruff
- mypy

不安装 SQLAlchemy、psycopg、Alembic、pgvector、OpenAI SDK 或前端依赖。

`pyproject.toml` 统一 pytest、Ruff 和 mypy 配置。测试从仓库根目录运行，导入路径必须由工程配置解决，测试文件不得临时修改 `sys.path`。

## 8. 验收命令

在项目 Conda 环境中，从仓库根目录执行：

```powershell
pytest test/backend
ruff check core/backend test/backend
mypy core/backend/app
python -c "from core.backend.app.main import app; print(app.title)"
```

验收标准：

- 所有命令退出码为 0；
- 无数据库、OpenAI 或 Node 依赖；
- 八个真实 YAML 能通过启动校验；
- 领域约束和信息隐藏测试完整；
- 子智能体提交实现说明、自审发现和未完成项；
- 主会话独立阅读代码与测试，不直接接受子智能体结论。

## 9. 延后项目

下一阶段才讨论并实现：

- 后台真实时间循环与 Day 事件调度；
- NPC 位置、寻路和邀请；
- 原始聊天消息与片段压缩；
- 数据库 Schema 与持久化；
- AI 决策、提示词和长期记忆召回；
- 前端及其 WebSocket 消费逻辑。
