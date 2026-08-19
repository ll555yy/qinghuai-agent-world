# 后端第一阶段主会话验收

> 历史阶段报告：以下结论只描述 2026-08-18 的第一阶段，不代表当前仓库状态。当前状态以 README 和最新专项验收文档为准。

- 状态：通过
- 日期：2026-08-18
- 实现：Luna Max 子智能体
- 最终验收：主会话

## 子智能体交付

- FastAPI 应用与启动场景校验；
- 内存 Run、WorldClock、Conversation 和事件；
- REST/WebSocket 接口；
- 八个场景 YAML 的读取与交叉引用校验；
- 19 项单元和集成测试；
- Ruff、mypy 与导入检查。

## 主会话发现并修正

1. 初稿只保留 NPC 公开身份，丢弃了人设、说话风格、边界和核心秘密；增加内部 `NpcPersonaDefinition`，同时保持公开快照不泄露。
2. Run 在 `run_created` 事件写入前先进入仓储，存在并发读取半初始化状态的窗口；改为完成初始状态后再发布 Run。
3. WebSocket 在订阅和发送首帧快照之间可能把快照已经包含的事件再次发送；增加按首帧 `eventSeq` 过滤。
4. 删除没有旧客户端依据的 `minutes` 兼容字段、CreateRun 未使用的 `commandId` 和 DELETE 命令的重复参数入口。
5. 公开 Actor 状态改为字段级投影，避免未来在内部状态增加私有字段时被整体复制到客户端。

## 主会话复验

```text
pytest test/backend -q
19 passed, 1 third-party deprecation warning

ruff check core/backend test/backend
All checks passed!

mypy core/backend/app
Success: no issues found in 30 source files

python -c "from core.backend.app.main import app; print(app.title)"
Qinghuai Chat Backend
```

## 边界结论

- 未连接数据库；
- 未创建前端；
- 该阶段验收时尚未接入 AI；
- 运行状态重启后丢失，符合阶段设计；
- 已具备下一阶段增加火山方舟适配器的稳定边界。
