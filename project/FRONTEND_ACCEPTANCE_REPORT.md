# 第一版前端验收报告

日期：2026-08-22

## 1. 交付范围

第一版浏览器前端已完成，技术栈为 React 19、TypeScript 5.9、Vite 8、Phaser 3.90、Zustand 5、REST + WebSocket、Vitest、Playwright 和 pnpm 11。

已实现：

- 开局前情提要、五项玩家任务和旁观路线。
- 单一慎之旧书店二维场景、六名 Actor、权威坐标同步、移动插值、聊天圈、状态和头顶气泡。
- NPC 右键菜单、公开信息卡；不渲染 Goal、关系、秘密或 Memory。
- 玩家邀请、接受/拒绝 NPC 邀请、申请加入、审批第三人加入、最多三人聊天。
- 玩家可读历史、自由发言、加入/离开系统消息、关闭原因和日终强制结束表现。
- 20 分钟世界日的时间推进、17:00 截止新聊天、17:50 提醒、18:00 日终和公共事件记录。
- 页面刷新恢复 Run、WebSocket `afterSeq` 重放、1/2/5 秒断线重连和普通错误提示。
- Day7 分支、五项主张采纳、五人最终公开立场、玩家任务结果和玩家关键发言记录。

后端同步增加了开局公开场景接口、重连所需的玩家待处理请求、公开结局立场/发言投影，以及 Windows PostgreSQL 模式的兼容启动入口。

## 2. 目录

```text
core/frontend/
  src/api/       REST、WebSocket、OpenAPI 生成类型和公开消费类型
  src/game/      Phaser 书店场景与 React 宿主
  src/state/     权威快照/事件 Store 与本地 UI Store
  src/ui/        开局、任务、世界、聊天、人物、事件和结局界面
  scripts/       可自动回收 Vite 的 E2E 启动器
test/frontend/
  unit/          Vitest / Testing Library
  e2e/           Playwright 入口
project/
  FRONTEND_DESIGN.md
  FRONTEND_EVENT_MAPPING.md
  FRONTEND_IMPLEMENTATION_PLAN.md
  FRONTEND_ACCEPTANCE_REPORT.md
```

## 3. 前后端连接

- 开发端口：前端 `127.0.0.1:5173`，FastAPI `127.0.0.1:8000`。
- Vite 将 `/api` 和 `/ws` 转发到 FastAPI。
- 后端公开快照是唯一权威状态；前端事件 reducer 仅按 `eventSeq` 应用公开投影。
- OpenAPI 已生成到 `core/frontend/src/api/generated.ts`；由于部分后端响应仍声明为 `dict[str, Any]`，Run/Event 继续使用窄公开类型和运行时守卫。
- Windows 数据库模式使用 `python -m core.backend.app`，由项目入口创建 psycopg 兼容的 Selector 事件循环。

## 4. 自动化门禁

| 门禁 | 结果 |
|---|---|
| `pnpm install --frozen-lockfile` | 通过，锁文件无变化 |
| `pnpm lint` | 通过，0 warning |
| `pnpm typecheck` | 通过 |
| `pnpm test` | 5 个文件、11 个测试通过 |
| `pnpm build` | 通过 |
| `pnpm test:e2e` | 7 个核心流程通过 |
| 右键聊天流程稳定性 | 连续重复 3 次通过 |
| 后端 Pytest | 150 通过、10 个需专用外部环境的用例跳过 |
| Ruff | 通过 |
| mypy | 63 个源文件通过 |

Playwright 覆盖任务选择、进入世界、刷新恢复、人物公开卡、邀请与真实聊天 UI、加入并读取旧记录、WebSocket 日终、Day7 结局和后端不可用错误页。

## 5. 真实联调

使用 Docker PostgreSQL、真实 FastAPI 和已配置火山方舟完成一个临时 Run：

- 健康检查为 `ok`，开局接口返回 5 个 Agenda 和 5 个公开 NPC。
- 创建玩家与林慧兰的会话并发送玩家发言；最终公开记录为 2 条消息，其中 1 条为真实 NPC 回复。
- WebSocket 返回权威快照：6 个 Actor，事件序号正常。
- 浏览器目检通过：提要和任务页正常渲染，5 项主张均显示提出人，控制台无 warning/error。
- 临时 Run 按精确 ID 删除，数据库返回 `DELETE 1`；报告不保存 API Key、密码、私有提示词或 NPC 私有上下文。

## 6. 已知限制

- 后端没有玩家任意移动命令；第一版只在邀请/加入过程中播放靠近表现，不在前端伪造权威坐标。
- 玩家发言与结局之间只展示可验证的发言、最终立场和采纳结果，不声称严格因果归因。
- Phaser 使世界页面生产 chunk 约 1.22 MB（gzip 约 326 KB），构建有体积提示但不影响第一版运行；后续可拆 Phaser 场景资源。
- 头像和书店地图为可替换占位资源，尚无复杂人物动画、地图编辑和移动端深度适配。
- 后端部分 Response Model 仍是宽字典；前端已做边界守卫，后续应把公开响应逐步改为 Pydantic 模型。

## 7. 本地启动

```powershell
# 1. 数据库
docker compose up -d database

# 2. 后端（仓库根目录）
conda activate qinghuai-chat
python -m core.backend.app

# 3. 前端（另一个终端）
cd core/frontend
pnpm install
pnpm dev
```

打开 `http://127.0.0.1:5173`。运行门禁时使用 `pnpm lint`、`pnpm typecheck`、`pnpm test`、`pnpm build` 和 `pnpm test:e2e`。
