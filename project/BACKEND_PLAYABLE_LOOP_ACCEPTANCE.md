# 七天 NPC 聊天世界后端可玩闭环验收

> 历史阶段记录：本文的“未纳入数据库/真实模型”和“下一阶段先做前端”只适用于当时。当前 PostgreSQL、pgvector、Agent、真实模型和七日模拟门禁状态以 `PRE_FRONTEND_BACKEND_ACCEPTANCE.md` 为准。

- 状态：主会话验收通过
- 日期：2026-08-19
- 运行范围：FastAPI、内存 Run、火山方舟 TextModel 端口、REST/WebSocket、自动测试
- 未纳入：数据库、前端、真实网络模型质量测试、自动真实时间后台循环

## 1. 验收结论

当前版本已经能在不连接数据库、前端和真实模型网络的条件下，通过 API 和 Fake TextModel 完整运行 Day1 至 Day7。主要闭环为：

```text
开局公告 → NPC 每日错峰思考 → 移动与邀请 → 两/三人聊天
→ 玩家加入和发言 → 私有草稿与记忆召回 → 离场沉淀
→ 后续世界事件 → Day7 固定结算
```

模型缺少配置、超时、限流或结构错误时采用简单安全结果，不生成虚构台词、Memory 或章节立场。结构化文本错误最多重新生成一次；Ark 适配器已经完成瞬时网络重试后，编排层不会再次叠加网络重试。没有引入熔断、多模型复核、分布式锁、多级缓存或兼容层。

## 2. 需求对应证据

1. **时间和行动**：现实 1 秒映射世界 1 分钟；08:00 至 18:00 为 600 秒。五名 NPC 每日分别在 09/11/13/15/17 点思考一次，Day1 公告先于首个思考。
2. **移动和邀请**：顺序为 `actor_movement_started → actor_movement_completed → invitation_requested → invitation_request_cleared → accepted/refused`；拒绝不创建聊天。
3. **聊天人数和加入**：世界最多两场聊天，每场最多三人。第三名 NPC 直接加入，原成员收到 `actor_joined` 决策上下文；新 NPC 看不到加入前消息，玩家主动加入后得到整场历史。
4. **聊天决策和离开**：`ChatDecision` 只使用 `speak | wait | leave_chat`，可同时产生 Goal、多个关系维度、待创建短期 Goal 和章节立场草稿；所有 Goal 终结后 NPC 标记为 `departed`。
5. **草稿和沉淀**：合法草稿立即覆盖同场后续提示词；NPC 离场时一次提交正式 Goal、关系、熟悉度和章节状态，并创建 owner 强制归属的原子 Memory 与 Goal/Actor/Topic 链接。
6. **事件和结算**：Day1 至 Day7 事件来自 YAML。public 事件进入公共状态；observed 事件只进入当时半径内 NPC 的 Memory 和私有世界状态。Day7 直接读取已提交章节表，固定计算共识、妥协或未提交，以及五项主张和玩家任务结果。
7. **模型接入和兜底**：六类 Pydantic 协议均为 `extra=forbid`；实际 TextModel 使用现有 ArkClient 和 `doubao-seed-2.0-lite`。缺少密钥和模型错误不会阻塞无模型演示。
8. **信息隔离**：公共快照、REST、WebSocket 和事件不复制人设秘密、深层 Goal、私有 Memory、关系数值、提示词、内部意图或章节态度矩阵。后台 NPC 消息在玩家不在场时只公开为 `conversation_activity`。

## 3. 主会话独立修正

- 让 observed 世界状态按角色保存，避免未目击玩家从公共快照看到事件结果。
- 让第三名 NPC 只读取加入后的原文和自己曾参与片段的摘要；玩家加入仍读取完整历史。
- 对 Memory 强制 owner 隔离，并限制 Memory 只能关联当前 NPC 自己的 Goal。
- 关系草稿只允许指向当前聊天参与者，同一决策同一关系维度只变化一步。
- 草稿提交、互动次数和失败重试使用明确状态位，避免重复应用。
- Day7 结算不检索 Graph、不调用模型补猜缺失立场，公开结果不包含内部统计矩阵。
- 将模型失败控制为一次必要重试和协议安全结果，避免 Ark 网络重试与结构化重试叠加。
- 在任何移动或气泡事件前校验玩家邀请冲突；接受邀请时先验证会话容量和参与者状态，失败不会留下孤立的 accepted 邀请。
- 通用 Conversation 接口同样拒绝 `departed` NPC，并要求先处理角色已有的 pending 邀请。

## 4. 自动验证结果

在 Conda 环境 `qinghuai-chat` 中从仓库根目录执行：

```text
python -m pytest -q
53 passed, 1 warning in 4.30s

python -m ruff check core/backend/app test/backend
All checks passed!

python -m mypy core/backend/app
Success: no issues found in 41 source files
```

唯一 warning 来自 FastAPI TestClient 依赖中的 Starlette/httpx 迁移提示，不影响当前逻辑和测试结果。测试没有访问真实方舟网络。

覆盖的关键证据包括：五人每日一次思考、事件先后顺序、邀请拒绝顺序、observed 事件私有性、第三人和玩家的不同历史可见性、owner 限定的单次记忆召回、聊天草稿即时生效与一次提交、原子 Memory 和短期 Goal 触发链接、NPC 离开世界、三种 Day7 大结局，以及无效模型输出的简单回退。

## 5. 安全检查

- 仓库扫描未发现用户先前在聊天中提供的方舟密钥或相同片段。
- `.env.example` 只有占位符；`.gitignore` 排除 `.env` 和日志。
- 应用公共接口只返回安全模型状态，不返回 API Key。
- Git 仓库已经初始化；当前交付文件已落盘，但本验收没有替用户创建远程仓库或推送。

## 6. 当前明确边界

- Run 仍在单进程内存中，服务重启后丢失。
- 无前端时由 `world/step` 驱动时间，不在后台真实等待 70 分钟。
- observed 事件第一版以书店中心固定半径判断，没有逐帧寻路或复杂场景遮挡。
- Graph v1 是 owner 过滤后的内存节点和链接检索，没有 pgvector 与 Embedding 排序。
- 真实方舟效果、延迟和费用尚未测试；必须先轮换聊天中暴露过的旧 Key，再在本机设置新 Key。

下一阶段适合先做最小 React/Phaser 场景与聊天面板，使当前 API 闭环真正可由玩家操作；数据库持久化可以在玩法体验验证后接入。
