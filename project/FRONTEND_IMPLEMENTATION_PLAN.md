# 第一版前端实施计划

> 顺序：设计先行，随后骨架、通信、主流程、测试和真实联调。
> 完成定义：可从开局进入世界，观察与聊天，并到达 Day7 结局；质量命令全部通过。

## 1. 工程骨架

- 在 `core/frontend` 建立 Vite React TypeScript 工程。
- 安装 React、Phaser、Zustand；开发依赖加入 ESLint、Vitest、Testing Library、Playwright 和 OpenAPI 类型生成工具。
- 配置 `/api`、`/ws` 到 `http://127.0.0.1:8000`。
- 脚本：`dev`、`build`、`lint`、`typecheck`、`test`、`test:e2e`、`generate:api`。
- 测试代码统一放在 `test/frontend`；Playwright 配置从仓库根路径读取该目录。

验收：空应用可启动，六项命令存在，build/typecheck/lint/Vitest 通过。

## 2. 通信和状态

- 实现 `ApiClient`、统一 `ApiError`、UUID commandId。
- 实现 WebSocket 快照/事件判别、eventSeq 去重、afterSeq 重连和手动恢复。
- 建立 Zustand slices：run、actors、conversations、requests、connection、ui。
- 建立事件 reducer；Phaser 只订阅场景投影。
- 世界步进控制器每 2 个有效前台秒串行提交一次，页面隐藏或请求进行中时不累积。

验收：单元测试覆盖快照替换、事件去重、断线状态、时间推进和消息合并。

## 3. 开局和世界场景

- 实现前情提要、公开主张选择、旁观选项和 Run 创建。
- Phaser 绘制书店占位场景、六个 Actor、名字、状态和聊天圈。
- 实现服务器坐标到场景坐标映射与 movement 事件动画。
- 实现右键命中并打开 React 菜单。

验收：可创建真实 Run；场景显示公开快照中的六个角色，不读取 YAML 私有人物字段。

## 4. NPC 交互和聊天

- NPC 公开信息抽屉。
- 玩家邀请、玩家响应 NPC 邀请、拒绝/过期反馈。
- 玩家申请加入聊天、玩家审批 NPC 加入请求。
- 加载可读历史、发送自由文本、离开聊天。
- 展示参与者变化、关闭原因、静默和非文本活动。

验收：用受控 API Mock 跑完邀请、加入、发言、离开；再用真实 FastAPI 完成至少一次玩家聊天。

## 5. 事件、日终和结局

- 世界事件横幅及记录抽屉。
- 17:00/17:50 提醒、18:00 日终遮罩、次日过渡。
- departed 表现。
- Day7 结局：分支、五项公开主张采纳结果、五人最终公开立场、玩家任务结果和玩家关键发言记录。

验收：单元测试覆盖时间边界与结局映射；E2E 可用 Mock 快进至 Day7。

## 6. E2E 与最终联调

Playwright 覆盖：

1. 提要 → 选择任务 → 进入世界。
2. 查看 NPC 公开信息。
3. 邀请 NPC 并看到接受/拒绝。
4. 申请加入、读取历史、发送消息、离开。
5. 接收世界事件和日终事件。
6. 渲染 Day7 结局。
7. 后端不可用和 WebSocket 重连。

真实联调使用一个临时 Run；不在日志或报告中输出 NPC 私有上下文、API Key 或数据库凭据。用户已明确授权清理本次临时 Run，因此验收后按精确 Run ID 从本地开发数据库删除，不触碰其他数据。

最终门禁：

```text
pnpm install
pnpm lint
pnpm typecheck
pnpm test
pnpm build
pnpm test:e2e
```

并检查：真实 FastAPI 连接、WebSocket 持续事件、一次真实聊天、私有字段扫描、设计与实现同步。

## 7. 当前非阻塞协议差距

| 差距 | 第一版处理 | 后续方向 |
|---|---|---|
| 无玩家自由移动命令 | 仅播放邀请/加入靠近表现，不建立本地权威坐标 | 增加后端 player movement command |
| Run 响应多为 Any 字典 | 前端窄类型 + 守卫 | 后端增加明确 Response Model |
| 玩家聊天影响难以做严格因果归因 | 展示后端结算立场、采纳结果与玩家真实发言记录 | 后续增加带证据链的公开影响投影 |
| 无 Run 删除 API | Mock E2E；真实联调避免大量临时 Run | 增加仅开发环境可用的清理机制 |

这些差距不会通过读取数据库、复制私有状态或伪造剧情来绕过。

开局主张原本只能在 Run 创建后查询，与“先选择任务再创建 Run”冲突；实施时已增加只读 `GET /api/scenario/agendas`。该接口仅复用 `public_agenda()` 投影，不增加新的权威状态。

Windows 下 Uvicorn 默认可能先创建 psycopg 不兼容的 Proactor 事件循环；最终实现增加 `python -m core.backend.app` 启动入口，明确使用 Selector 事件循环。该入口是 Windows 本地 PostgreSQL 模式的标准启动方式。
