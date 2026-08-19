# 青槐老巷聊天世界

当前仓库按职责分为三个主要目录：

- `core/`：前后端代码、运行时场景配置和生成类型。
- `test/`：所有测试、模拟和测试数据。
- `project/`：已确认的设计文档与一致性审计。

当前已完成无需数据库和前端即可运行的七天后端可玩闭环：世界时间与事件、NPC 错峰行动、移动和邀请、最多三人聊天、玩家加入与自由发言、私有记忆召回、离场沉淀以及 Day7 固定结算。真实模型通过火山方舟 `doubao-seed-2.0-lite` 接入；未设置密钥时使用不编造剧情的安全结果。

后端仍使用单进程内存状态，重启会丢失 Run；PostgreSQL 和 React/Phaser 前端属于后续阶段。权威玩法见 `project/PROJECT_DESIGN.md`，实施原则见 `project/PROJECT_RULES.md`，本阶段设计与验收见 `project/BACKEND_PLAYABLE_LOOP_DESIGN.md` 和 `project/BACKEND_PLAYABLE_LOOP_ACCEPTANCE.md`。

## 本地验证

在 Conda 环境 `qinghuai-chat` 中，从仓库根目录运行：

```powershell
python -m pytest -q
python -m ruff check core/backend/app test/backend
python -m mypy core/backend/app
```

需要启动 API 时：

```powershell
python -m uvicorn core.backend.app.main:app --reload
```

需要真实模型时，复制 `.env.example` 到本机 `.env`，填写已经轮换的新 `ARK_API_KEY`，并使用：

```powershell
python -m uvicorn core.backend.app.main:app --reload --env-file .env
```

不要把 `.env` 加入 Git。
