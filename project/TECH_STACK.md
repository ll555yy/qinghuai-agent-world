# 第一版技术栈（已确认）

> 状态：已确认。  
> 确认时间：2026-08-18。  
> 目标：用最少的基础设施完成单场景、6 个角色、最多 2 场并行聊天、7 天章节与 Graph 记忆原型。

## 1. 推荐结论

第一版采用“浏览器优先、TypeScript 前端、Python 单体后端、单 PostgreSQL 数据库”：

| 层级 | 推荐技术 | 负责内容 |
|---|---|---|
| 前端外壳 | React + Vite + TypeScript | 聊天框、右键菜单、任务选择、角色信息、结局页 |
| 2D 场景 | Phaser 3 | 地图、头像、移动、靠近检测、聊天圈与气泡 |
| 客户端状态 | Zustand | 当前 UI、连接状态、前端临时交互；不保存权威世界状态 |
| 后端 | Python 3.12+ + FastAPI | 世界时钟、NPC 状态机、聊天编排、模型调用、结局结算 |
| ASGI 服务 | Uvicorn | 运行 FastAPI，承载 HTTP 与 WebSocket |
| 实时通信 | FastAPI WebSocket | 世界状态增量、聊天消息、模型流式文本与气泡事件 |
| 普通接口 | REST | 开局载入、存档读取、配置检查、重开章节 |
| 后端数据契约 | Pydantic v2 | API、WebSocket、模型结构化输出与配置文件校验的单一来源 |
| 前端契约 | Pydantic 导出的 JSON Schema → TypeScript 类型 | 避免 Python 和 TypeScript 手写两套协议 |
| 数据库 | PostgreSQL + pgvector | 角色、Goal、关系、聊天、Memory Graph、向量与章节状态 |
| 数据访问 | SQLAlchemy 2 Async + psycopg 3 | 常规 CRUD 和事务；递归 CTE 与向量查询使用 SQLAlchemy Core/原生 SQL |
| 数据迁移 | Alembic | 表结构和索引版本管理 |
| 模型接口 | 火山方舟 Agent Plan（OpenAI 兼容） | 通过独立适配器调用 Doubao 模型，不进入领域层 |
| Agent 编排 | LangGraph 1.x `StateGraph` | 编排 NPC 每日行动、邀请响应、聊天决策与只读记忆工具；不拥有世界状态 |
| 后端测试 | pytest + AnyIO/HTTPX | 领域逻辑、异步 API、WebSocket 和确定性章节模拟 |
| 前端测试 | Vitest + Playwright | 前端单测和关键 UI 流程 |
| Python 环境 | Anaconda/Conda + `environment.yml` | Python 解释器、隔离环境与依赖复现 |
| 本地环境 | Docker Compose | 只启动 PostgreSQL/pgvector；应用仍直接本地运行 |

## 2. 为什么第一版不使用独立游戏引擎

当前画面只有一个 2D 场景、头像移动和简单碰撞，复杂度主要在聊天 UI、异步状态和 AI 编排，而不是物理、动画或关卡编辑。React 处理聊天界面更直接，Phaser 足以处理场景部分，两者可以在同一页面并存。

若未来明确要发布 Steam 桌面版，可在 Web 原型稳定后再使用 Tauri/Electron 封装；这不影响 Python 后端的领域模型。

## 3. 为什么 Graph 第一版放在 PostgreSQL

这里需要的是“逻辑上的 Graph”，并不等于必须先上 Neo4j：

- Memory、Actor、Goal、Topic 是普通记录；
- `memory_actor_links`、`memory_goal_links`、`memory_topic_links` 和 `memory_edges` 表达边；
- PostgreSQL 递归 CTE 完成 1—2 跳扩展；
- pgvector 完成语义候选检索；
- PostgreSQL 事务保证 NPC 离场时 Memory、Goal、关系和章节立场只提交一次。

第一版不引入 Neo4j，避免双数据库同步、事务边界和部署复杂度。只有在实际数据达到较大规模、查询超过 2—3 跳且 PostgreSQL 查询被测出瓶颈后，才重新评估专用图数据库。

## 4. 为什么第一版不引入 Redis/消息队列

一个章节最多 6 个角色、2 场 NPC 并行聊天。Python 后端进程内使用：

- 每个对话一个 `asyncio.Queue` 串行处理新消息；
- 全局 `asyncio.Semaphore` 限制并发模型调用；
- `runId + conversationId + eventSeq + stateVersion` 丢弃过期结果；
- 请求超时和一次重试。

这已经足够。需要多后端实例或后台离线批处理时，再引入 Redis/BullMQ。

## 5. 当前 AI 模型基线

| 配置 | 当前值 |
|---|---|
| Provider | 火山方舟 Agent Plan |
| Base URL | `https://ark.cn-beijing.volces.com/api/plan/v3` |
| Model | `doubao-seed-2.0-lite` |
| API Key | 仅从 `ARK_API_KEY` 环境变量读取 |
| 协议 | OpenAI 兼容协议，通过独立 Python 适配器调用 |

六类 NPC 调用初期全部使用这一小模型，先进行质量、延迟、格式正确率和成本评测。若某类调用明显不达标，再只升级该调用，不提前设计多模型路由。Embedding 模型在数据库与长期记忆阶段另行选择。

不使用通用 ReAct Agent、Agents SDK、Realtime API 或服务端自动托管的长对话状态。当前使用自定义 LangGraph `StateGraph` 编排三个有边界的 NPC Agent 入口和一次只读记忆工具调用；Python 世界编排器继续掌握聊天片段、缓存、Goal 草稿、关系草稿、移动、会话约束和离场事务。方舟返回结果必须经过 Pydantic 校验后才能进入系统。

## 6. 数据契约策略

Python 与 TypeScript 之间不手写重复接口：

1. 后端在 `app/contracts/` 定义 Pydantic 模型；
2. HTTP 接口由 FastAPI 自动生成 OpenAPI；
3. WebSocket 事件联合类型由 Pydantic 导出 JSON Schema；
4. 构建脚本根据 Schema 生成前端 TypeScript 类型；
5. CI 检查生成文件是否与 Pydantic 定义同步；
6. 模型结构化输出、后端内部验证和前端事件类型尽量复用同一份字段定义。

权威状态仍只在 Python 后端。前端生成出来的类型是消费协议，不拥有世界规则。

## 7. 已确认的仓库结构

```text
core/
  backend/             FastAPI 世界编排器和 API
    app/
      api/             REST / WebSocket 入口
      contracts/       Pydantic 契约与模型输出 Schema
      domain/          世界时间、聊天、Goal、关系、章节纯逻辑
      orchestration/   NPC 决策与消息流水线
      persistence/     SQLAlchemy 仓储与事务
    migrations/        Alembic 迁移
    environment.yml    Conda 环境定义
  frontend/            React + Phaser 客户端
    src/
      game/            Phaser 二维场景
      ui/              React 界面
      state/           Zustand 状态
      api/             后端通信
  scenario/            运行时场景 YAML
  generated/           Schema 和前端生成类型
test/
  backend/             后端单元与集成测试
  frontend/            前端测试
  simulation/          七天自动模拟
  e2e/                 Playwright 完整流程
  fixtures/            测试假数据
project/
  PROJECT_DESIGN.md    权威玩法与技术决定
  SYSTEM_DESIGN.md     系统实现设计
  TECH_STACK.md        已确认技术栈
  CONSISTENCY_AUDIT.md 一致性审计
```

前端使用 pnpm，Python 后端使用独立 Conda 环境与 `environment.yml`，不使用 Conda 的 `base` 环境。`core/backend/app/domain` 不依赖 FastAPI、SQLAlchemy 或 OpenAI SDK，数据库和模型能力通过接口注入，保证大逻辑能用确定性测试验证。AI 尚未进入实现阶段，因此当前不创建 `prompts` 或模型调用目录。

## 8. 两个备选方案

### 备选 A：纯 React，不使用 Phaser

用 CSS/Canvas 做头像移动。初期代码更少，但聊天圈、路径、场景坐标和后续动画容易逐渐变成自制小游戏引擎。若确定永远只有极简平面，可以采用；否则不推荐。

### 备选 B：Godot 客户端 + Python 后端

地图、动画和桌面发布能力更强，但需要维护 GDScript/C# 客户端与 Python 后端两套工程，聊天 UI 和 WebSocket 调试也更重。只有当重点转向场景交互、动画和多地图时才值得采用。

## 9. 已确认的技术决策

第一版采用以下组合：

1. 浏览器优先的 React + Phaser 客户端；
2. Python + FastAPI 单体权威后端；
3. PostgreSQL + pgvector，同库实现关系数据、向量和逻辑 Graph；
4. 第一版不使用 Neo4j、Redis、Agents SDK 和 Realtime API；
5. 使用 Pydantic 作为契约源，导出 Schema 和前端 TypeScript 类型；
6. 初期统一使用 `doubao-seed-2.0-lite`，后续根据模拟评测只升级不达标的调用。
